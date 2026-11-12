"""install_windows.py — install CORRECT as a Windows service, for all users.

Normally started by setup.cmd / setup.ps1, which make sure an all-users Python 3.11
exists and that we are elevated. Stdlib only: it runs on the base interpreter and
builds the application venv itself.

Layout (mirrors install.sh / the OVA layout):

    C:\\Program Files\\CORRECT\\app\\src        application source (copied from this repo)
    C:\\Program Files\\CORRECT\\app\\venv       Python virtualenv (+ pythonservice.exe)
    C:\\ProgramData\\CORRECT\\config.yaml       runtime config (kept across re-installs)
    C:\\ProgramData\\CORRECT\\working           in-progress studies + quarantine
    C:\\ProgramData\\CORRECT\\logs              application + transaction logs
    C:\\ProgramData\\CORRECT\\shared            reports + anonymized PID mapping

Re-running the installer is the update path: code and dependencies are refreshed,
config and data are preserved.

Examples (elevated prompt):
    setup.cmd                                       # LocalSystem, port 11112
    setup.cmd --account virtual                     # NT SERVICE\\CORRECT (least privilege)
    setup.cmd --account user --user CONTOSO\\svc_correct
    setup.cmd --account user --user CONTOSO\\correct-gmsa$
    setup.cmd --config D:\\site\\config.yaml --port 11112
    setup.cmd --dry-run
"""

import argparse
import ctypes
import getpass
import os
import re
import shutil
import struct
import subprocess
import sys
import time

SERVICE_NAME = "CORRECT"
FIREWALL_RULE = "CORRECT DICOM"
ARP_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\CORRECT"
SETTINGS_KEY = r"SOFTWARE\CORRECT"
PASSWORD_ENV = "CORRECT_SERVICE_PASSWORD"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "__pycache__", "*.pyc", "*.egg-info", ".pytest_cache",
    ".ipynb_checkpoints", "UID_*", "data", "netrt_data", "*.log", ".claude",
)

DRY_RUN = False


# ── helpers ─────────────────────────────────────────────────────────────────
def step(n, total, msg):
    print(f"==> [{n}/{total}] {msg}", flush=True)


def info(msg):
    print(f"   {msg}", flush=True)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, check=True, env=None, cwd=None, quiet=False):
    print(">> " + subprocess.list2cmdline(cmd), flush=True)
    if DRY_RUN:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    proc = subprocess.run(cmd, env=env, cwd=cwd, text=True,
                          capture_output=quiet)
    if check and proc.returncode != 0:
        if quiet:
            print((proc.stdout or "") + (proc.stderr or ""), file=sys.stderr)
        die(f"command failed with exit code {proc.returncode}")
    return proc


def do(desc, fn, *a, **kw):
    print(f">> {desc}", flush=True)
    if not DRY_RUN:
        return fn(*a, **kw)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def read_version():
    try:
        with open(os.path.join(REPO_ROOT, "netrt_core", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
            return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def service_installed():
    proc = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True)
    return proc.returncode == 0


def stop_service_if_running():
    if not service_installed():
        return
    proc = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True)
    if "STOPPED" in proc.stdout:
        return
    run(["sc.exe", "stop", SERVICE_NAME], check=False, quiet=True)
    if DRY_RUN:
        return
    for _ in range(120):
        proc = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True)
        if "STOPPED" in proc.stdout:
            return
        time.sleep(1)
    die("service did not stop within 2 minutes; stop it manually and re-run")


def sid_principal(sid):
    return f"*{sid}"


# ── steps ───────────────────────────────────────────────────────────────────
def copy_source(source, src_dir):
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(src_dir)):
        info("installing from the installed copy itself — leaving source in place")
        return
    if os.path.isdir(src_dir):
        do(f"remove old source {src_dir}", shutil.rmtree, src_dir)
    do(f"copy {source} -> {src_dir}", shutil.copytree, source, src_dir, ignore=COPY_IGNORE)


def build_venv(venv_dir, src_dir):
    py = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(py):
        run([sys.executable, "-m", "venv", venv_dir])
    else:
        info(f"venv exists: {venv_dir}")
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-r", os.path.join(src_dir, "requirements.txt")])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "-e", src_dir])
    return py


def install_config(config_path, data_dir, custom_config, port):
    if custom_config:
        if not os.path.isfile(custom_config):
            die(f"custom config not found: {custom_config}")
        do(f"copy {custom_config} -> {config_path}", shutil.copy2, custom_config, config_path)
        return "custom"
    if os.path.exists(config_path):
        info(f"{config_path} already exists — preserving it.")
        return "kept"
    template = os.path.join(HERE, "config.windows.example.yaml")
    with open(template, encoding="utf-8") as f:
        text = f.read()
    default_root = r"C:\ProgramData\CORRECT"
    if os.path.normcase(data_dir) != os.path.normcase(default_root):
        text = text.replace(default_root, data_dir.replace("'", "''"))
    # dicom_listener.port is the first "port:" in the template.
    text = re.sub(r"(?m)^(\s+port:\s*)11112\b", lambda m: m.group(1) + str(port), text, count=1)

    def write():
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(text)
    do(f"write template config -> {config_path}", write)
    return "template"


def harden_data_root(data_dir):
    """Data may contain PHI: only Administrators and SYSTEM by default (service account added later)."""
    run(["icacls", data_dir, "/inheritance:r",
         "/grant:r", f"{sid_principal('S-1-5-32-544')}:(OI)(CI)F",
         "/grant:r", f"{sid_principal('S-1-5-18')}:(OI)(CI)F", "/Q"], quiet=True)


def configure_firewall(port):
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"], check=False, quiet=True)
    run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in",
         "action=allow", "protocol=TCP", f"localport={port}", "profile=any"], quiet=True)


def create_shortcuts(venv_dir, logs_dir, config_path):
    programs = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                            r"Microsoft\Windows\Start Menu\Programs\CORRECT")
    gui = os.path.join(venv_dir, "Scripts", "correct-config.exe")
    links = [
        ("CORRECT Configuration", gui, f'--config "{config_path}"'),
        ("CORRECT Logs", logs_dir, ""),
    ]
    ps = [f"New-Item -ItemType Directory -Force -Path '{programs}' | Out-Null",
          "$sh = New-Object -ComObject WScript.Shell"]
    for name, target, arguments in links:
        lnk = os.path.join(programs, name + ".lnk")
        ps += [f"$l = $sh.CreateShortcut('{lnk}')",
               f"$l.TargetPath = '{target}'",
               f"$l.Arguments = '{arguments}'",
               "$l.Save()"]
    run(["powershell.exe", "-NoProfile", "-Command", "; ".join(ps)], quiet=True)
    return programs


def register_arp(install_dir, data_dir, config_path, version):
    import winreg
    script = os.path.join(install_dir, "app", "src", "install", "windows", "uninstall_windows.py")
    # Settings > Apps does not elevate on its own, so go through UAC explicitly.
    uninstall = ("powershell.exe -NoProfile -Command \"Start-Process -Verb RunAs "
                 f"-FilePath '{sys.executable}' -ArgumentList '\\\"{script}\\\"'\"")
    values = {
        "DisplayName": "CORRECT DICOM RT Structure Processor",
        "DisplayVersion": version,
        "Publisher": "CORRECT",
        "InstallLocation": install_dir,
        "UninstallString": uninstall,
        "NoModify": 1,
        "NoRepair": 1,
    }

    def write():
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, ARP_KEY, 0,
                                winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            for name, val in values.items():
                kind = winreg.REG_DWORD if isinstance(val, int) else winreg.REG_SZ
                winreg.SetValueEx(k, name, 0, kind, val)
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, SETTINGS_KEY, 0,
                                winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, "InstallDir", 0, winreg.REG_SZ, install_dir)
            winreg.SetValueEx(k, "DataDir", 0, winreg.REG_SZ, data_dir)
            winreg.SetValueEx(k, "ConfigPath", 0, winreg.REG_SZ, config_path)
            winreg.SetValueEx(k, "Version", 0, winreg.REG_SZ, version)
    do(f"register Add/Remove Programs entry HKLM\\{ARP_KEY}", write)


_READ_CONFIG = r"""
import json, sys
from netrt_core.config_loader import load_config
c = load_config(sys.argv[1])
print(json.dumps({
    "port": c["dicom_listener"].get("port"),
    "working": c["directories"].get("working"),
    "logs": c["directories"].get("logs"),
    "reports": c.get("reporting", {}).get("output_directory"),
    "pid_mapping_file": c["anonymization"].get("pid_mapping_file"),
    "destination": c["dicom_destination"].get("ip"),
}))
"""


def read_config(venv_py, src_dir, config_path):
    """Effective settings, parsed by the app's own loader (with defaults merged)."""
    if DRY_RUN or not os.path.exists(config_path):
        return {}
    import json
    proc = subprocess.run([venv_py, "-c", _READ_CONFIG, config_path], cwd=src_dir,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        info(f"WARNING: could not parse {config_path}: {proc.stderr.strip()[-300:]}")
        return {}
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── main ────────────────────────────────────────────────────────────────────
def main():
    global DRY_RUN
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")

    p = argparse.ArgumentParser(description="Install CORRECT as a Windows service (all users).",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--source", default=REPO_ROOT, help="CORRECT source tree to install (default: this repo)")
    p.add_argument("--install-dir", default=os.path.join(program_files, "CORRECT"))
    p.add_argument("--data-dir", default=os.path.join(program_data, "CORRECT"),
                   help="config + working/logs/shared root (keep it short: MAX_PATH)")
    p.add_argument("--config", help="install this config file instead of the template")
    p.add_argument("--port", type=int, default=11112, help="listener port for a new config + firewall rule")
    p.add_argument("--account", choices=("localsystem", "virtual", "user"), default="localsystem",
                   help="service logon account (default: localsystem)")
    p.add_argument("--user", help=r"with --account user: DOMAIN\name, .\localname or DOMAIN\gmsa$")
    p.add_argument("--no-firewall", action="store_true", help="do not add a Windows Firewall rule")
    p.add_argument("--no-start", action="store_true", help="register the service but do not start it")
    p.add_argument("--dry-run", action="store_true", help="print every step, change nothing")
    args = p.parse_args()
    DRY_RUN = args.dry_run

    if sys.platform != "win32":
        die("this installer is for Windows; on Linux use install/install.sh")
    if not is_admin() and not DRY_RUN:
        die("must run as Administrator (use setup.cmd, which elevates).")
    if sys.version_info[:2] != (3, 11) or struct.calcsize("P") != 8:
        die(f"needs 64-bit Python 3.11 (this is {sys.version.split()[0]}, {struct.calcsize('P') * 8}-bit)")
    if args.account == "user" and not args.user:
        die("--account user needs --user")
    if not os.path.isfile(os.path.join(args.source, "main.py")):
        die(f"not a CORRECT source tree: {args.source}")

    install_dir = os.path.abspath(args.install_dir)
    data_dir = os.path.abspath(args.data_dir)
    app_dir = os.path.join(install_dir, "app")
    src_dir = os.path.join(app_dir, "src")
    venv_dir = os.path.join(app_dir, "venv")
    config_path = os.path.join(data_dir, "config.yaml")
    working, logs, shared = (os.path.join(data_dir, d) for d in ("working", "logs", "shared"))
    total = 9

    # Collect a password up front (never on the command line); gMSA accounts have none.
    child_env = dict(os.environ)
    if args.account == "user" and not args.user.endswith("$") and not DRY_RUN:
        child_env[PASSWORD_ENV] = os.environ.get(PASSWORD_ENV) or getpass.getpass(f"Password for {args.user}: ")

    print(f"Installing CORRECT {read_version()} -> {install_dir}  (data: {data_dir})")
    step(1, total, "stopping existing service (if any)")
    stop_service_if_running()

    step(2, total, "copying application source")
    copy_source(os.path.abspath(args.source), src_dir)

    step(3, total, "creating virtualenv and installing dependencies (several minutes)")
    venv_py = build_venv(venv_dir, src_dir)

    step(4, total, "creating data directories")
    for d in (data_dir, working, logs, shared):
        do(f"mkdir {d}", os.makedirs, d, exist_ok=True)
    harden_data_root(data_dir)

    step(5, total, "installing config")
    config_state = install_config(config_path, data_dir, args.config, args.port)
    cfg = read_config(venv_py, src_dir, config_path)
    port = int(cfg.get("port") or args.port)

    step(6, total, f"registering Windows service '{SERVICE_NAME}' (account: {args.account})")
    cmd = [venv_py, "-m", "correct_admin.service_control", "register", "--config", config_path,
           "--account", args.account]
    if args.user:
        cmd += ["--user", args.user]
    run(cmd, env=child_env, cwd=src_dir)
    # Folders the service account needs (configured paths may point elsewhere, e.g. D:\).
    writable = [working, logs, shared]
    pid = cfg.get("pid_mapping_file")
    for v in (cfg.get("working"), cfg.get("logs"), cfg.get("reports"), pid and os.path.dirname(pid)):
        # UNC shares are skipped: their ACLs are managed on the file server.
        if v and os.path.isabs(v) and not v.startswith("\\\\"):
            writable.append(v)
    run([venv_py, "-m", "correct_admin.service_control", "acl",
         "--writable", *sorted(set(writable)), "--readable", data_dir], cwd=src_dir)

    step(7, total, f"firewall rule for TCP {port}")
    if args.no_firewall:
        info("skipped (--no-firewall)")
    else:
        configure_firewall(port)

    step(8, total, "Start Menu shortcuts (all users) + Add/Remove Programs entry")
    programs = create_shortcuts(venv_dir, logs, config_path)
    register_arp(install_dir, data_dir, config_path, read_version())

    step(9, total, "starting service")
    if args.no_start:
        info("skipped (--no-start)")
    else:
        proc = run([venv_py, "-m", "correct_admin.service_control", "start"], check=False, cwd=src_dir)
        if proc.returncode != 0:
            info(f"service did not start — check Event Viewer (Application log) and {logs}")

    needs_edit = config_state == "template" or str(cfg.get("destination", "")).startswith("YOUR_")
    print()
    print("==================== INSTALL COMPLETE ====================")
    print(f"  Service:   sc query {SERVICE_NAME}    (services.msc: \"CORRECT DICOM RT Structure Processor\")")
    print(f"  Config:    {config_path}" + ("   <-- EDIT: set dicom_destination, site_code" if needs_edit else ""))
    print(f"  Config UI: Start Menu > CORRECT > CORRECT Configuration  ({programs})")
    print(f"  Logs:      {logs}  + Event Viewer > Windows Logs > Application")
    print(f"  Listener:  netstat -ano | findstr :{port}")
    print(f"  Test:      echoscu -aet SENDER -aec CORRECT <this-host> {port}")
    print(f"  Uninstall: Settings > Apps, or python \"{os.path.join(src_dir, 'install', 'windows', 'uninstall_windows.py')}\"")
    print("==========================================================")
    if DRY_RUN:
        print("(dry-run: nothing was actually changed)")


if __name__ == "__main__":
    main()

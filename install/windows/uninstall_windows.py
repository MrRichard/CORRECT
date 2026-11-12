"""uninstall_windows.py — remove the CORRECT Windows service and program files.

Keeps C:\\ProgramData\\CORRECT (config, PID mapping, logs, reports) unless --purge.

    python uninstall_windows.py              # asks for confirmation
    python uninstall_windows.py --yes
    python uninstall_windows.py --purge      # also delete config + data (PHI!)
    python uninstall_windows.py --dry-run
"""

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time

SERVICE_NAME = "CORRECT"
FIREWALL_RULE = "CORRECT DICOM"
ARP_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\CORRECT"
SETTINGS_KEY = r"SOFTWARE\CORRECT"

DRY_RUN = False


def run(cmd, check=False):
    print(">> " + subprocess.list2cmdline(cmd), flush=True)
    if DRY_RUN:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def do(desc, fn, *a, **kw):
    print(f">> {desc}", flush=True)
    if not DRY_RUN:
        return fn(*a, **kw)


def read_settings():
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SETTINGS_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            for name in ("InstallDir", "DataDir", "ConfigPath"):
                try:
                    out[name] = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    pass
    except OSError:
        pass
    return out


def delete_key(path):
    import winreg
    try:
        winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, path, winreg.KEY_WOW64_64KEY, 0)
    except OSError:
        pass


def remove_service():
    q = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True)
    if q.returncode != 0:
        print("   service not installed")
        return
    run(["sc.exe", "stop", SERVICE_NAME])
    if not DRY_RUN:
        for _ in range(120):
            q = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True)
            if "STOPPED" in q.stdout or q.returncode != 0:
                break
            time.sleep(1)
    run(["sc.exe", "delete", SERVICE_NAME])


def main():
    global DRY_RUN
    p = argparse.ArgumentParser(description="Uninstall CORRECT (Windows).")
    p.add_argument("--purge", action="store_true", help="also delete config and ALL data (working studies, PID map, logs)")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    DRY_RUN = args.dry_run

    if sys.platform != "win32":
        sys.exit("ERROR: Windows only; on Linux use install/uninstall.sh")
    if not DRY_RUN and not ctypes.windll.shell32.IsUserAnAdmin():
        sys.exit("ERROR: run as Administrator")

    settings = read_settings()
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")
    install_dir = settings.get("InstallDir") or os.path.join(program_files, "CORRECT")
    data_dir = settings.get("DataDir") or os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "CORRECT")

    print(f"This removes the CORRECT service and {install_dir}.")
    print(f"Data in {data_dir} will be " + ("DELETED (--purge)." if args.purge else "kept."))
    if not args.yes and not DRY_RUN:
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("aborted")

    print("==> stopping and removing service")
    remove_service()
    print("==> removing firewall rule")
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"])
    print("==> removing Start Menu shortcuts and Add/Remove Programs entry")
    programs = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                            r"Microsoft\Windows\Start Menu\Programs\CORRECT")
    if os.path.isdir(programs):
        do(f"rmdir {programs}", shutil.rmtree, programs, ignore_errors=True)
    do(f"delete HKLM\\{ARP_KEY}", delete_key, ARP_KEY)
    do(f"delete HKLM\\{SETTINGS_KEY}", delete_key, SETTINGS_KEY)
    print("==> removing program files")
    if os.path.isdir(install_dir):
        do(f"rmdir {install_dir}", shutil.rmtree, install_dir, ignore_errors=True)
    if args.purge and os.path.isdir(data_dir):
        print("==> purging data")
        do(f"rmdir {data_dir}", shutil.rmtree, data_dir, ignore_errors=True)

    print("Uninstall complete." + ("" if args.purge else f" Config and data kept in {data_dir}."))
    if DRY_RUN:
        print("(dry-run: nothing was actually changed)")


if __name__ == "__main__":
    main()

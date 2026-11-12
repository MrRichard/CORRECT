"""Install, remove and control the CORRECT service on Windows (SCM) or Linux (systemd).

Windows install scopes (chosen in the installer, recorded in install.json):

* "user"    — installed for the current user; the service logs on AS that user.
              Registering needs admin once (UAC); afterwards the user is granted
              start/stop rights on the service, so day-to-day control needs no admin.
* "machine" — installed for all users; the service runs as LocalSystem by default,
              or the NT SERVICE\\CORRECT virtual account / a named user or gMSA.

The config UI calls these functions directly when it is already elevated and runs
this module as an elevated CLI (see correct_admin/elevated.py) when it is not:

    python -m correct_admin.service_control init-config
    python -m correct_admin.service_control install-service --scope machine --account localsystem
    python -m correct_admin.service_control install-service --scope user --user DOMAIN\\me
    python -m correct_admin.service_control set-account --account virtual
    python -m correct_admin.service_control remove-service
    python -m correct_admin.service_control status | start | stop | restart | account
"""

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys

from . import LINUX_UNIT_NAME, SERVICE_DESCRIPTION, SERVICE_DISPLAY_NAME, SERVICE_NAME

IS_WINDOWS = sys.platform == "win32"

ACCOUNT_LOCALSYSTEM = "localsystem"
ACCOUNT_VIRTUAL = "virtual"
ACCOUNT_USER = "user"
ACCOUNT_KINDS = (ACCOUNT_LOCALSYSTEM, ACCOUNT_VIRTUAL, ACCOUNT_USER)

VIRTUAL_ACCOUNT_NAME = f"NT SERVICE\\{SERVICE_NAME}"
PASSWORD_ENV = "CORRECT_SERVICE_PASSWORD"

# Service rights for a non-admin user: query config/status, start, stop,
# interrogate, read permissions.
USER_SERVICE_RIGHTS = "CCLCRPWPLORC"


class ServiceError(RuntimeError):
    pass


class Cancelled(ServiceError):
    pass


# ════════════════════════════════════════════════════════════════════════════
# pure helpers (unit-tested on any OS)
# ════════════════════════════════════════════════════════════════════════════
def account_name_for(kind, user=None):
    """SCM logon name for an account kind."""
    if kind == ACCOUNT_LOCALSYSTEM:
        return "LocalSystem"
    if kind == ACCOUNT_VIRTUAL:
        return VIRTUAL_ACCOUNT_NAME
    if kind == ACCOUNT_USER:
        if not user:
            raise ServiceError("a user name is required for --account user")
        if "\\" not in user and "@" not in user:
            user = ".\\" + user  # local account
        return user
    raise ServiceError(f"unknown account kind: {kind}")


def classify_account(name):
    """Map an SCM logon name back to (kind, user)."""
    if not name or name.lower() in ("localsystem", "nt authority\\system"):
        return ACCOUNT_LOCALSYSTEM, None
    if name.lower() == VIRTUAL_ACCOUNT_NAME.lower():
        return ACCOUNT_VIRTUAL, None
    return ACCOUNT_USER, name


def icacls_principal(account):
    """Principal string icacls understands for an SCM logon name."""
    if account.lower() in ("localsystem", "nt authority\\system"):
        return "*S-1-5-18"
    if account.startswith(".\\"):
        return os.environ.get("COMPUTERNAME", ".") + account[1:]
    return account


def is_gmsa(user):
    return bool(user) and user.endswith("$")


def add_allow_ace(sddl, sid, rights=USER_SERVICE_RIGHTS):
    """Return ``sddl`` with an allow ACE for ``sid`` added to its DACL (idempotent).

    Example: "D:(A;;CCLC;;;SY)S:(AU;FA;CCLC;;;WD)" + SID -> the new ACE is appended
    at the end of the D: section, before any S: (SACL) section.
    """
    ace = f"(A;;{rights};;;{sid})"
    if ace.lower() in sddl.lower():
        return sddl
    m = re.search(r"D:[A-Z]*", sddl)
    if not m:
        raise ServiceError(f"service security descriptor has no DACL: {sddl!r}")
    sacl = re.search(r"S:", sddl[m.end():])
    insert_at = m.end() + sacl.start() if sacl else len(sddl)
    return sddl[:insert_at] + ace + sddl[insert_at:]


def config_paths(config_path):
    """Folders the service writes to, from the effective config (defaults merged)."""
    from netrt_core.config_loader import load_config
    cfg = load_config(config_path)
    dirs = cfg.get("directories", {})
    pid = cfg.get("anonymization", {}).get("pid_mapping_file")
    out = [dirs.get("working"), dirs.get("logs"),
           cfg.get("reporting", {}).get("output_directory"),
           os.path.dirname(pid) if pid else None]
    port = cfg.get("dicom_listener", {}).get("port", 11112)
    # UNC shares are left alone: their ACLs belong on the file server.
    return [os.path.expanduser(p) for p in out if p and not str(p).startswith("\\\\")], int(port)


# ════════════════════════════════════════════════════════════════════════════
# Windows
# ════════════════════════════════════════════════════════════════════════════
def _winerror(e):
    return getattr(e, "winerror", None) or (e.args[0] if getattr(e, "args", None) else None)


def is_admin():
    if not IS_WINDOWS:
        return os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def current_user():
    """DOMAIN\\name of the logged-on user running this process."""
    import win32api
    import win32con
    return win32api.GetUserNameEx(win32con.NameSamCompatible)


def account_sid_string(account):
    import win32security
    lookup = account
    if lookup.startswith(".\\"):
        lookup = os.environ.get("COMPUTERNAME", "") + lookup[1:]
    sid = win32security.LookupAccountName(None, lookup)[0]
    return win32security.ConvertSidToStringSid(sid)


def validate_credentials(account, password):
    """Check a user's password before handing it to the SCM (avoids error 1069 at start)."""
    import win32con
    import win32security
    if "\\" in account:
        domain, name = account.split("\\", 1)
        if domain == ".":
            domain = os.environ.get("COMPUTERNAME", ".")
    else:
        domain, name = None, account
    try:
        handle = win32security.LogonUser(name, domain, password,
                                         win32con.LOGON32_LOGON_NETWORK,
                                         win32con.LOGON32_PROVIDER_DEFAULT)
        handle.Close()
    except Exception as e:
        code = _winerror(e)
        if code == 1385:  # logon type not granted: password is right, policy blocks network logon
            return
        if code == 1327 and not password:
            raise ServiceError(
                f"{account} has no password. Windows services cannot log on to an account "
                "without a password — set one (Ctrl+Alt+Del > Change a password) and try again.")
        if code in (1326, 1327):
            raise ServiceError(f"The password for {account} is incorrect.")
        if code == 1909:
            raise ServiceError(f"{account} is locked out.")
        raise ServiceError(f"Could not verify the credentials for {account}: {e}")


def grant_service_logon_right(account):
    """Grant SeServiceLogonRight; ChangeServiceConfig does not do this automatically."""
    import win32security
    lookup = account
    if lookup.startswith(".\\"):
        lookup = os.environ.get("COMPUTERNAME", "") + lookup[1:]
    sid = win32security.LookupAccountName(None, lookup)[0]
    policy = win32security.LsaOpenPolicy(None, win32security.POLICY_ALL_ACCESS)
    try:
        win32security.LsaAddAccountRights(policy, sid, ["SeServiceLogonRight"])
    finally:
        win32security.LsaClose(policy)


def grant_user_service_control(service, account):
    """Let ``account`` start/stop/query the service without admin rights."""
    sid = account_sid_string(account)
    proc = _run(["sc.exe", "sdshow", service])
    sddl = "".join(line.strip() for line in proc.stdout.splitlines() if line.strip())
    new = add_allow_ace(sddl, sid)
    if new != sddl:
        _run(["sc.exe", "sdset", service, new])


def apply_windows_acls(account, writable_dirs, readable_paths=()):
    """Give ``account`` Modify on ``writable_dirs`` (inherited) and Read on ``readable_paths``."""
    principal = icacls_principal(account)
    for d in writable_dirs:
        if not d:
            continue
        os.makedirs(d, exist_ok=True)
        _run(["icacls", d, "/grant", f"{principal}:(OI)(CI)M", "/Q"])
    for p in readable_paths:
        if p and os.path.exists(p):
            flags = "(OI)(CI)RX" if os.path.isdir(p) else "R"
            _run(["icacls", p, "/grant", f"{principal}:{flags}", "/Q"])


def pythonservice_exe():
    """pythonservice.exe next to python.exe (placed there by packaging/windows/build.ps1).

    For a development venv it is copied there from the pywin32 package on demand,
    together with the DLLs it needs.
    """
    import win32service
    target_dir = sys.exec_prefix
    exe = os.path.join(target_dir, "pythonservice.exe")
    pywin32_dir = os.path.dirname(win32service.__file__)
    site_packages = os.path.dirname(pywin32_dir)
    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    needed = {
        "pythonservice.exe": [os.path.join(pywin32_dir, "pythonservice.exe")],
        f"pywintypes{ver}.dll": [os.path.join(site_packages, "pywin32_system32", f"pywintypes{ver}.dll")],
        f"python{ver}.dll": [os.path.join(sys.base_exec_prefix, f"python{ver}.dll")],
        "python3.dll": [os.path.join(sys.base_exec_prefix, "python3.dll")],
    }
    for name, sources in needed.items():
        dst = os.path.join(target_dir, name)
        if os.path.exists(dst):
            continue
        for src in sources:
            if os.path.exists(src):
                shutil.copy2(src, dst)
                break
    if not os.path.exists(exe):
        raise ServiceError(f"pythonservice.exe not found in {target_dir}")
    return exe


class WindowsServiceController:
    def __init__(self, name=SERVICE_NAME):
        self.name = name

    # -- queries ---------------------------------------------------------------
    def is_installed(self):
        import win32serviceutil
        try:
            win32serviceutil.QueryServiceStatus(self.name)
            return True
        except Exception as e:
            if _winerror(e) == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
                return False
            raise

    def status(self):
        import win32service
        import win32serviceutil
        if not self.is_installed():
            return "not installed"
        state = win32serviceutil.QueryServiceStatus(self.name)[1]
        return {
            win32service.SERVICE_STOPPED: "stopped",
            win32service.SERVICE_START_PENDING: "starting",
            win32service.SERVICE_STOP_PENDING: "stopping",
            win32service.SERVICE_RUNNING: "running",
            win32service.SERVICE_CONTINUE_PENDING: "resuming",
            win32service.SERVICE_PAUSE_PENDING: "pausing",
            win32service.SERVICE_PAUSED: "paused",
        }.get(state, f"unknown ({state})")

    def get_account(self):
        import win32service
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            svc = win32service.OpenService(scm, self.name, win32service.SERVICE_QUERY_CONFIG)
            try:
                return win32service.QueryServiceConfig(svc)[7]
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)

    # -- control ---------------------------------------------------------------
    def start(self):
        import win32service
        import win32serviceutil
        if self.status() == "running":
            return
        win32serviceutil.StartService(self.name)
        win32serviceutil.WaitForServiceStatus(self.name, win32service.SERVICE_RUNNING, 60)

    def stop(self):
        import win32service
        import win32serviceutil
        if self.status() in ("stopped", "not installed"):
            return
        win32serviceutil.StopService(self.name)
        win32serviceutil.WaitForServiceStatus(self.name, win32service.SERVICE_STOPPED, 120)

    def restart(self):
        self.stop()
        self.start()

    # -- registration ----------------------------------------------------------
    def register(self, config_path, kind=ACCOUNT_LOCALSYSTEM, user=None, password=None):
        """Create (or update) the pywin32 service pointing at ``config_path``."""
        import win32service
        import win32serviceutil
        from . import winservice

        class_string = win32serviceutil.GetServiceClassString(winservice.CorrectService)
        account = account_name_for(kind, user)
        password = _password_for(kind, password)
        if kind == ACCOUNT_USER:
            grant_service_logon_right(account)

        common = dict(
            startType=win32service.SERVICE_AUTO_START,
            userName=account,
            password=password,
            exeName=pythonservice_exe(),
            description=SERVICE_DESCRIPTION,
            delayedstart=True,
        )
        if self.is_installed():
            win32serviceutil.ChangeServiceConfig(class_string, self.name, displayName=SERVICE_DISPLAY_NAME, **common)
        else:
            win32serviceutil.InstallService(class_string, self.name, SERVICE_DISPLAY_NAME, **common)
        win32serviceutil.SetServiceCustomOption(self.name, "ConfigPath", os.path.abspath(config_path))

        # Restart on crash (10s, 10s, 60s); failureflag=1 also treats a non-zero exit
        # code on stop (e.g. a startup exception) as a failure.
        _run(["sc.exe", "failure", self.name, "reset=", "86400",
              "actions=", "restart/10000/restart/10000/restart/60000"])
        _run(["sc.exe", "failureflag", self.name, "1"])
        return account

    def unregister(self):
        import win32serviceutil
        if not self.is_installed():
            return
        self.stop()
        win32serviceutil.RemoveService(self.name)

    def set_account(self, kind, user=None, password=None):
        """Change the logon account. Takes effect at the next service start."""
        import win32service
        account = account_name_for(kind, user)
        password = _password_for(kind, password)
        if kind == ACCOUNT_USER:
            grant_service_logon_right(account)
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            svc = win32service.OpenService(scm, self.name, win32service.SERVICE_CHANGE_CONFIG)
            try:
                win32service.ChangeServiceConfig(
                    svc,
                    win32service.SERVICE_NO_CHANGE,
                    win32service.SERVICE_NO_CHANGE,
                    win32service.SERVICE_NO_CHANGE,
                    None, None, 0, None,
                    account, password, None,
                )
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
        return account


def _password_for(kind, password):
    if kind in (ACCOUNT_LOCALSYSTEM, ACCOUNT_VIRTUAL):
        return ""
    if password is None:
        password = os.environ.get(PASSWORD_ENV)
    return password or ""  # gMSA accounts (DOMAIN\name$) have no password


# ── high-level flows used by the UI and the CLI ─────────────────────────────
def install_service(config_path, scope, kind=ACCOUNT_LOCALSYSTEM, user=None, password=None,
                    firewall=True, start=True):
    """Register the service and everything it needs. Must run elevated. Returns the account."""
    from netrt_core.platform_paths import SCOPE_USER
    from .setup_tasks import init_config, set_firewall_rule

    if scope == SCOPE_USER:
        kind = ACCOUNT_USER
        if not user:
            raise ServiceError("--user is required for a current-user install")
    if not os.path.exists(config_path):
        init_config(os.path.dirname(os.path.abspath(config_path)), scope)

    account = account_name_for(kind, user)
    if kind == ACCOUNT_USER and not is_gmsa(user):
        validate_credentials(account, _password_for(kind, password))

    ctl = WindowsServiceController()
    ctl.register(config_path, kind, user, password)
    writable, port = config_paths(config_path)

    if scope == SCOPE_USER:
        # Data lives in the user's own profile; just let them control the service.
        grant_user_service_control(ctl.name, account)
    elif kind != ACCOUNT_LOCALSYSTEM:
        config_dir = os.path.dirname(os.path.abspath(config_path))
        apply_windows_acls(account, writable, [config_dir, config_path])

    if firewall:
        set_firewall_rule(port)
    if start:
        ctl.start()
    return account


def change_account(config_path, kind, user=None, password=None, restart=True):
    """Switch the logon account of an installed service (machine scope). Must run elevated."""
    account = account_name_for(kind, user)
    if kind == ACCOUNT_USER and not is_gmsa(user):
        validate_credentials(account, _password_for(kind, password))
    ctl = WindowsServiceController()
    ctl.set_account(kind, user, password)
    if kind != ACCOUNT_LOCALSYSTEM:
        writable, _ = config_paths(config_path)
        config_dir = os.path.dirname(os.path.abspath(config_path))
        apply_windows_acls(account, writable, [config_dir, config_path])
    if restart and ctl.status() != "stopped":
        ctl.restart()
    elif restart:
        ctl.start()
    return account


def remove_service():
    """Stop and delete the service and its firewall rule. Must run elevated."""
    from .setup_tasks import remove_firewall_rule
    WindowsServiceController().unregister()
    remove_firewall_rule()


# ════════════════════════════════════════════════════════════════════════════
# Linux (systemd)
# ════════════════════════════════════════════════════════════════════════════
class SystemdServiceController:
    def __init__(self, name=LINUX_UNIT_NAME):
        self.name = name

    def _systemctl(self, *args, check=True):
        if not shutil.which("systemctl"):
            raise ServiceError("systemctl not found")
        proc = subprocess.run(["systemctl", *args], capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise ServiceError((proc.stderr or proc.stdout).strip() or f"systemctl {' '.join(args)} failed")
        return proc

    def is_installed(self):
        try:
            proc = self._systemctl("show", "-p", "LoadState", "--value", self.name, check=False)
        except ServiceError:
            return False
        return proc.stdout.strip() == "loaded"

    def status(self):
        if not self.is_installed():
            return "not installed"
        proc = self._systemctl("is-active", self.name, check=False)
        state = proc.stdout.strip() or "unknown"
        return {"active": "running", "inactive": "stopped", "activating": "starting",
                "deactivating": "stopping"}.get(state, state)

    def get_account(self):
        proc = self._systemctl("show", "-p", "User", "--value", self.name, check=False)
        return proc.stdout.strip() or "root"

    def start(self):
        self._systemctl("start", self.name)

    def stop(self):
        self._systemctl("stop", self.name)

    def restart(self):
        self._systemctl("restart", self.name)

    def set_account(self, kind, user=None, password=None):
        raise ServiceError("On Linux the service account is set by install.sh (--user).")


def get_controller():
    return WindowsServiceController() if IS_WINDOWS else SystemdServiceController()


# ════════════════════════════════════════════════════════════════════════════
# helpers + CLI
# ════════════════════════════════════════════════════════════════════════════
def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ServiceError(f"{' '.join(cmd)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def _resolve_password(args):
    """Password for --account user, from env, a GUI prompt, or the console. Never argv."""
    if getattr(args, "account", None) != ACCOUNT_USER or is_gmsa(args.user):
        return None
    if os.environ.get(PASSWORD_ENV) is not None:
        return os.environ[PASSWORD_ENV]
    account = account_name_for(ACCOUNT_USER, args.user)
    if getattr(args, "prompt_password", False):
        from .elevated import ask_password_gui
        pw = ask_password_gui(account)
        if pw is None:
            raise Cancelled("cancelled")
        return pw
    return getpass.getpass(f"Windows password for {account}: ")


def _default_config():
    from netrt_core.platform_paths import default_config_path
    return default_config_path()


def _default_scope():
    from netrt_core.platform_paths import SCOPE_MACHINE, install_scope
    return install_scope() or SCOPE_MACHINE


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m correct_admin.service_control")
    parser.add_argument("--result-file", help=argparse.SUPPRESS)  # JSON outcome for the UI
    parser.add_argument("--elevate-if-needed", action="store_true",
                        help="re-run through UAC when not elevated (Windows)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-config", help="create data folders and config.yaml if missing")
    p_init.add_argument("--data-dir")
    p_init.add_argument("--port", type=int)

    p_inst = sub.add_parser("install-service", help="register, configure and start the Windows service")
    p_inst.add_argument("--scope", choices=("user", "machine"))
    p_inst.add_argument("--config")
    p_inst.add_argument("--no-firewall", action="store_true")
    p_inst.add_argument("--no-start", action="store_true")

    p_acct = sub.add_parser("set-account", help="change the Windows service logon account")
    p_acct.add_argument("--config")
    for p in (p_inst, p_acct):
        p.add_argument("--account", choices=ACCOUNT_KINDS, default=ACCOUNT_LOCALSYSTEM)
        p.add_argument("--user", help="DOMAIN\\name, .\\localname or DOMAIN\\gmsa$")
        p.add_argument("--prompt-password", action="store_true", help=argparse.SUPPRESS)

    p_fw = sub.add_parser("firewall", help="add/update or remove the inbound firewall rule")
    g = p_fw.add_mutually_exclusive_group(required=True)
    g.add_argument("--port", type=int)
    g.add_argument("--remove", action="store_true")

    p_acl = sub.add_parser("acl", help="grant the service account access to folders")
    p_acl.add_argument("--writable", nargs="*", default=[])
    p_acl.add_argument("--readable", nargs="*", default=[])

    sub.add_parser("remove-service", help="stop and delete the service + firewall rule")
    for name in ("start", "stop", "restart", "status", "account"):
        sub.add_parser(name)
    return parser


def _execute(args):
    """Run one command; returns a short message."""
    if args.cmd == "init-config":
        from .setup_tasks import init_config
        path, created = init_config(args.data_dir, port=args.port)
        return f"{'created' if created else 'exists'}: {path}"
    if args.cmd == "status":
        return get_controller().status()
    if args.cmd == "account":
        return get_controller().get_account()
    if args.cmd in ("start", "stop", "restart"):
        getattr(get_controller(), args.cmd)()
        return f"{args.cmd}: ok"
    if not IS_WINDOWS:
        raise ServiceError(f"'{args.cmd}' is only available on Windows")

    if args.cmd == "install-service":
        scope = args.scope or _default_scope()
        user = args.user
        if scope == "user" and not user:
            user = current_user()
        if scope == "user":
            args.account = ACCOUNT_USER
            args.user = user
        account = install_service(args.config or _default_config(), scope, args.account, user,
                                  _resolve_password(args), firewall=not args.no_firewall,
                                  start=not args.no_start)
        return f"installed; logs on as {account}"
    if args.cmd == "set-account":
        return "logs on as " + change_account(args.config or _default_config(), args.account,
                                              args.user, _resolve_password(args))
    if args.cmd == "remove-service":
        remove_service()
        return "removed"
    if args.cmd == "firewall":
        from .setup_tasks import remove_firewall_rule, set_firewall_rule
        if args.remove:
            remove_firewall_rule()
            return "firewall rule removed"
        set_firewall_rule(args.port)
        return f"firewall rule allows TCP {args.port}"
    if args.cmd == "acl":
        apply_windows_acls(get_controller().get_account(), args.writable, args.readable)
        return "acl: ok"
    raise ServiceError(f"unknown command {args.cmd}")


def _say(message, ok=True):
    stream = sys.stdout if ok else sys.stderr
    if stream is not None:  # None under pythonw.exe (the elevated child)
        print(message, file=stream)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)

    if args.elevate_if_needed and IS_WINDOWS and not is_admin():
        if args.cmd == "remove-service" and not WindowsServiceController().is_installed():
            from .setup_tasks import firewall_rule_exists
            if not firewall_rule_exists():
                return 0  # nothing to do, no UAC prompt
        from .elevated import run_service_command
        rest = [a for a in argv if a != "--elevate-if-needed"]
        ok, message = run_service_command(rest)
        _say(message, ok)
        return 0 if ok else 1

    ok, message = True, ""
    try:
        message = _execute(args)
    except Cancelled as e:
        ok, message = False, str(e)
    except ServiceError as e:
        ok, message = False, str(e)
    except Exception as e:  # pywintypes.error etc.
        detail = getattr(e, "strerror", None) or str(e)
        ok, message = False, f"{type(e).__name__}: {detail}"

    if args.result_file:
        with open(args.result_file, "w", encoding="utf-8") as f:
            json.dump({"ok": ok, "message": message}, f)
    _say(message if ok else f"ERROR: {message}", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

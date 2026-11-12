"""Start/stop/inspect the CORRECT service on Windows (SCM) or Linux (systemd).

On Windows this module also registers the pywin32 service, changes its logon
account (LocalSystem, the NT SERVICE\\CORRECT virtual account, or a named
user/gMSA) and applies folder ACLs. It doubles as a small CLI used by the
Windows installer, which runs it with the venv's python.exe so that pywin32
registers pythonservice.exe from inside the venv:

    python -m correct_admin.service_control register --config C:\\ProgramData\\CORRECT\\config.yaml --account virtual
    python -m correct_admin.service_control set-account --account user --user DOMAIN\\svc_correct
    python -m correct_admin.service_control status
"""

import argparse
import getpass
import os
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


class ServiceError(RuntimeError):
    pass


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


# ════════════════════════════════════════════════════════════════════════════
# Windows
# ════════════════════════════════════════════════════════════════════════════
class WindowsServiceController:
    def __init__(self, name=SERVICE_NAME):
        self.name = name

    # -- queries ---------------------------------------------------------------
    def is_installed(self):
        import win32serviceutil
        try:
            win32serviceutil.QueryServiceStatus(self.name)
            return True
        except Exception as e:  # pywintypes.error 1060 = does not exist
            if getattr(e, "winerror", None) == 1060:
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
        import win32serviceutil
        win32serviceutil.StartService(self.name)
        win32serviceutil.WaitForServiceStatus(self.name, _running(), 60)

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
        """Install (or update) the pywin32 service and point it at ``config_path``."""
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
            description=SERVICE_DESCRIPTION,
            delayedstart=True,
        )
        if self.is_installed():
            win32serviceutil.ChangeServiceConfig(class_string, self.name, displayName=SERVICE_DISPLAY_NAME, **common)
        else:
            win32serviceutil.InstallService(class_string, self.name, SERVICE_DISPLAY_NAME, **common)
        win32serviceutil.SetServiceCustomOption(self.name, "ConfigPath", os.path.abspath(config_path))
        _ensure_pythonservice_runtime()

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


def _running():
    import win32service
    return win32service.SERVICE_RUNNING


def _password_for(kind, password):
    if kind in (ACCOUNT_LOCALSYSTEM, ACCOUNT_VIRTUAL):
        return ""
    if password is None:
        password = os.environ.get(PASSWORD_ENV)
    if password is None:
        password = ""  # gMSA accounts (DOMAIN\name$) have no password
    return password


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


def apply_windows_acls(account, writable_dirs, readable_paths=()):
    """Give ``account`` Modify on ``writable_dirs`` (inherited) and Read on ``readable_paths``.

    Administrators and SYSTEM keep full control through inheritance from ProgramData.
    """
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


def _ensure_pythonservice_runtime():
    """Make sure pythonservice.exe can start from the venv.

    pywin32 normally copies pythonservice.exe next to the venv's interpreter during
    InstallService. When it does not, the service fails with "module not found" /
    DLL errors, so copy the pieces it needs (pythonservice.exe, pywintypes3XX.dll,
    python3XX.dll, python3.dll) into sys.exec_prefix when they are missing.
    """
    import win32service  # noqa: F401  (locate pywin32)
    import win32serviceutil

    target_dir = sys.exec_prefix
    try:
        exe = win32serviceutil.LocatePythonServiceExe()
    except Exception:
        exe = None
    pywin32_dir = os.path.dirname(win32service.__file__)
    site_packages = os.path.dirname(pywin32_dir)
    candidates = [
        exe,
        os.path.join(pywin32_dir, "pythonservice.exe"),
    ]
    if not os.path.exists(os.path.join(target_dir, "pythonservice.exe")):
        for c in candidates:
            if c and os.path.exists(c):
                shutil.copy2(c, os.path.join(target_dir, "pythonservice.exe"))
                break

    ver = f"{sys.version_info.major}{sys.version_info.minor}"
    dll_sources = {
        f"pywintypes{ver}.dll": [os.path.join(site_packages, "pywin32_system32", f"pywintypes{ver}.dll")],
        f"pythoncom{ver}.dll": [os.path.join(site_packages, "pywin32_system32", f"pythoncom{ver}.dll")],
        f"python{ver}.dll": [os.path.join(sys.base_exec_prefix, f"python{ver}.dll")],
        "python3.dll": [os.path.join(sys.base_exec_prefix, "python3.dll")],
    }
    for name, sources in dll_sources.items():
        dst = os.path.join(target_dir, name)
        if os.path.exists(dst):
            continue
        for src in sources:
            if os.path.exists(src):
                shutil.copy2(src, dst)
                break


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
    if args.account != ACCOUNT_USER:
        return None
    if os.environ.get(PASSWORD_ENV) is not None:
        return os.environ[PASSWORD_ENV]
    if args.user and args.user.endswith("$"):
        return ""  # gMSA
    return getpass.getpass(f"Password for {args.user}: ")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m correct_admin.service_control")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="install/update the Windows service")
    p_reg.add_argument("--config", required=True)
    for p in (p_reg, sub.add_parser("set-account", help="change the Windows service logon account")):
        p.add_argument("--account", choices=ACCOUNT_KINDS, default=ACCOUNT_LOCALSYSTEM)
        p.add_argument("--user", help="DOMAIN\\name, .\\localname or DOMAIN\\gmsa$ (with --account user)")

    p_acl = sub.add_parser("acl", help="grant the service account access to data folders")
    p_acl.add_argument("--writable", nargs="*", default=[])
    p_acl.add_argument("--readable", nargs="*", default=[])

    sub.add_parser("unregister")
    for name in ("start", "stop", "restart", "status", "account"):
        sub.add_parser(name)

    args = parser.parse_args(argv)
    ctl = get_controller()
    try:
        if args.cmd == "register":
            print(ctl.register(args.config, args.account, args.user, _resolve_password(args)))
        elif args.cmd == "set-account":
            print(ctl.set_account(args.account, args.user, _resolve_password(args)))
        elif args.cmd == "acl":
            apply_windows_acls(ctl.get_account(), args.writable, args.readable)
        elif args.cmd == "unregister":
            ctl.unregister()
        elif args.cmd == "status":
            print(ctl.status())
        elif args.cmd == "account":
            print(ctl.get_account())
        else:
            getattr(ctl, args.cmd)()
    except ServiceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

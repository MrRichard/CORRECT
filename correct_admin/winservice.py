"""Windows service host for CORRECT (pywin32 ServiceFramework).

Registered by install/windows/install_windows.py via
``python -m correct_admin.service_control register``. The config path is read
from the service's Parameters registry key (ConfigPath).

Manual debugging from an elevated prompt (runs in the console, Ctrl+C to stop):
    "C:\\Program Files\\CORRECT\\app\\venv\\Scripts\\python.exe" -m correct_admin.winservice debug
"""

import os
import sys

# pythonservice.exe imports this file by path, so make sure the source root
# (which holds netrt_core/ and DicomAnonymizer.py) is importable.
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

import servicemanager  # noqa: E402
import win32event  # noqa: E402
import win32service  # noqa: E402
import win32serviceutil  # noqa: E402

from correct_admin import SERVICE_DESCRIPTION, SERVICE_DISPLAY_NAME, SERVICE_NAME  # noqa: E402
from netrt_core.platform_paths import default_config_path  # noqa: E402


class CorrectService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.app = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=60000)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        config_path = win32serviceutil.GetServiceCustomOption(
            self._svc_name_, "ConfigPath", default_config_path()
        )
        # Relative paths in the config resolve against the config's folder.
        os.chdir(os.path.dirname(os.path.abspath(config_path)) or _SRC_ROOT)

        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, f" (config: {config_path})"),
        )

        from netrt_core.app import CorrectApp  # heavy imports (numpy, rt_utils) after SCM handshake

        self.app = CorrectApp(config_path)
        try:
            self.app.start()
        except Exception as e:
            servicemanager.LogErrorMsg(f"{self._svc_name_} failed to start: {e!r}")
            try:
                self.app.stop()
            finally:
                # Re-raise so the SCM records a failure and recovery actions apply.
                raise

        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        self.app.stop()
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STOPPED,
            (self._svc_name_, ""),
        )


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Started by the SCM via pythonservice.exe with no arguments.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(CorrectService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(CorrectService)

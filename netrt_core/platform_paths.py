"""Per-platform default locations for CORRECT data and configuration.

On Linux the defaults match the install.sh / OVA layout. On Windows everything
lives under %ProgramData%\\CORRECT so it is shared by all users and reachable by
any service account (LocalSystem's "~" is C:\\Windows\\System32\\config\\systemprofile,
which is not a sensible place for study data).
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"


def windows_data_root():
    """Root data directory on Windows (C:\\ProgramData\\CORRECT by default)."""
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return os.path.join(program_data, "CORRECT")


def default_working_dir():
    if IS_WINDOWS:
        return os.path.join(windows_data_root(), "working")
    return "~/CORRECT_working"


def default_logs_dir():
    if IS_WINDOWS:
        return os.path.join(windows_data_root(), "logs")
    return "~/CORRECT_logs"


def default_shared_dir():
    """Directory for reports and the anonymized PID mapping."""
    if IS_WINDOWS:
        return os.path.join(windows_data_root(), "shared")
    return "/mnt/shared"


def default_pid_mapping_file():
    return os.path.join(default_shared_dir(), "pid_mapping.json")


def default_config_path():
    """Where an installed CORRECT keeps its runtime config."""
    if IS_WINDOWS:
        return os.path.join(windows_data_root(), "config.yaml")
    return "/opt/correct/app/config.yaml"

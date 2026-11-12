"""Per-platform default locations for CORRECT data and configuration.

On Linux the defaults match the install.sh / OVA layout. On Windows, data lives
under the directory chosen by the installer:

  * "All users" install:    %ProgramData%\\CORRECT
  * "Current user" install: %LOCALAPPDATA%\\CORRECT

The Windows installer records that choice in ``install.json`` at the install root
(the folder that contains ``app\\`` and ``runtime\\``). A development checkout has no
install.json and falls back to %ProgramData%\\CORRECT.
"""

import json
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

SCOPE_MACHINE = "machine"
SCOPE_USER = "user"

# <install root>\app\netrt_core\platform_paths.py -> parents[2] == <install root>
INSTALL_INFO_PATH = Path(__file__).resolve().parents[2] / "install.json"


def install_info(path=None):
    """Return the installer's metadata dict, or {} when not installed by the Windows installer."""
    if path is None:
        # Only an installed layout (<root>\app\netrt_core) can have install.json;
        # never pick up a stray file next to a development checkout.
        if Path(__file__).resolve().parents[1].name.lower() != "app":
            return {}
        path = INSTALL_INFO_PATH
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def install_scope():
    """"machine", "user", or None for a development checkout / Linux."""
    scope = install_info().get("scope")
    return scope if scope in (SCOPE_MACHINE, SCOPE_USER) else None


def install_root():
    """Install root when installed by the Windows installer, else None."""
    return str(INSTALL_INFO_PATH.parent) if install_info() else None


def windows_data_root():
    """Root data directory on Windows."""
    data_dir = install_info().get("data_dir")
    if data_dir:
        return data_dir
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

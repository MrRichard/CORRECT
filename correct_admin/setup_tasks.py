"""First-run setup tasks shared by the config UI and the service_control CLI.

* init_config(): create the data folders and write the Windows config template.
* Firewall rule management for the DICOM listener port.

The Windows installer (packaging/windows/correct.iss) only copies files; these steps
run on first launch of the CORRECT Configuration app.
"""

import os
import subprocess
import sys

from netrt_core.platform_paths import SCOPE_MACHINE, install_scope, windows_data_root

IS_WINDOWS = sys.platform == "win32"
FIREWALL_RULE = "CORRECT DICOM"
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "config.windows.yaml")
DATA_SUBDIRS = ("working", "logs", "shared")

ADMINISTRATORS_SID = "S-1-5-32-544"
SYSTEM_SID = "S-1-5-18"


def render_template(data_dir, port=None):
    """Windows config template text with ``data_dir`` filled in."""
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    # Paths sit inside single-quoted YAML scalars: escape ' as ''.
    text = text.replace("{DATA_DIR}", data_dir.rstrip("\\/").replace("'", "''"))
    if port is not None:
        lines = text.splitlines(keepends=True)
        in_listener = False
        for i, line in enumerate(lines):
            if line.startswith("dicom_listener:"):
                in_listener = True
            elif line and not line[0].isspace() and not line.startswith("#"):
                in_listener = False
            elif in_listener and line.strip().startswith("port:"):
                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = f"{indent}port: {int(port)}\n"
                break
        text = "".join(lines)
    return text


def init_config(data_dir=None, scope=None, port=None):
    """Create the data folders and, if missing, config.yaml. Returns (config_path, created).

    For an "All users" install the data root is locked down to Administrators and
    SYSTEM (it may hold PHI); the service account is added when the service is installed.
    """
    data_dir = os.path.abspath(data_dir or windows_data_root())
    scope = scope or install_scope()
    os.makedirs(data_dir, exist_ok=True)
    if IS_WINDOWS and scope == SCOPE_MACHINE:
        harden_data_root(data_dir)
    for sub in DATA_SUBDIRS:
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

    config_path = os.path.join(data_dir, "config.yaml")
    if os.path.exists(config_path):
        return config_path, False
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(render_template(data_dir, port))
    return config_path, True


def harden_data_root(data_dir):
    """Remove inherited ACEs (ProgramData lets Users create files) and grant only Admins + SYSTEM."""
    _run(["icacls", data_dir, "/inheritance:r",
          "/grant:r", f"*{ADMINISTRATORS_SID}:(OI)(CI)F",
          "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F", "/Q"])


# ── firewall ────────────────────────────────────────────────────────────────
def firewall_rule_exists():
    proc = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
                          capture_output=True, text=True)
    return proc.returncode == 0 and FIREWALL_RULE in proc.stdout


def set_firewall_rule(port):
    remove_firewall_rule()
    _run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in",
          "action=allow", "protocol=TCP", f"localport={int(port)}", "profile=any"])


def remove_firewall_rule():
    subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"],
                   capture_output=True, text=True)


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc

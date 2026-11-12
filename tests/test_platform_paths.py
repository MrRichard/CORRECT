import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from netrt_core import platform_paths as pp  # noqa: E402


def test_linux_defaults(monkeypatch):
    monkeypatch.setattr(pp, "IS_WINDOWS", False)
    assert pp.default_shared_dir() == "/mnt/shared"
    assert pp.default_working_dir() == "~/CORRECT_working"
    assert pp.default_config_path() == "/opt/correct/app/config.yaml"


def test_windows_defaults(monkeypatch):
    monkeypatch.setattr(pp, "IS_WINDOWS", True)
    monkeypatch.setenv("PROGRAMDATA", "D:\\PD")
    root = os.path.join("D:\\PD", "CORRECT")
    assert pp.windows_data_root() == root
    assert pp.default_working_dir() == os.path.join(root, "working")
    assert pp.default_logs_dir() == os.path.join(root, "logs")
    assert pp.default_pid_mapping_file() == os.path.join(root, "shared", "pid_mapping.json")
    assert pp.default_config_path() == os.path.join(root, "config.yaml")

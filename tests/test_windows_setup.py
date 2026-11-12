"""Pure logic behind the Windows installer flow (runs on any OS)."""

import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from correct_admin import elevated, setup_tasks  # noqa: E402
from correct_admin import service_control as sc  # noqa: E402
from correct_admin.config_model import ConfigModel  # noqa: E402
from netrt_core import platform_paths as pp  # noqa: E402

USER_SID = "S-1-5-21-111-222-333-1001"


# ── service security descriptor ─────────────────────────────────────────────
def test_ace_appended_before_sacl():
    sddl = "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCLCSWLOCRRC;;;IU)S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)"
    out = sc.add_allow_ace(sddl, USER_SID)
    assert out == ("D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCLCSWLOCRRC;;;IU)"
                   f"(A;;CCLCRPWPLORC;;;{USER_SID})"
                   "S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)")


def test_ace_without_sacl_and_idempotent():
    sddl = "D:(A;;CCLC;;;SY)"
    once = sc.add_allow_ace(sddl, USER_SID)
    assert once.endswith(f"(A;;CCLCRPWPLORC;;;{USER_SID})")
    assert sc.add_allow_ace(once, USER_SID) == once


def test_ace_keeps_dacl_flags():
    out = sc.add_allow_ace("D:P(A;;CC;;;SY)", USER_SID)
    assert out.startswith("D:P(A;;CC;;;SY)(A;;")


def test_ace_requires_dacl():
    with pytest.raises(sc.ServiceError):
        sc.add_allow_ace("O:SYG:SY", USER_SID)


# ── accounts ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,user,expected", [
    ("localsystem", None, "LocalSystem"),
    ("virtual", None, "NT SERVICE\\CORRECT"),
    ("user", "alice", ".\\alice"),
    ("user", "CONTOSO\\svc", "CONTOSO\\svc"),
    ("user", "CONTOSO\\gmsa$", "CONTOSO\\gmsa$"),
])
def test_account_names(kind, user, expected):
    assert sc.account_name_for(kind, user) == expected
    assert sc.classify_account(expected)[0] == kind


def test_gmsa_detection():
    assert sc.is_gmsa("CONTOSO\\correct$")
    assert not sc.is_gmsa("CONTOSO\\correct")


# ── install.json ────────────────────────────────────────────────────────────
def test_install_info_reads_file(tmp_path):
    f = tmp_path / "install.json"
    f.write_text(json.dumps({"scope": "user", "data_dir": "C:\\Users\\me\\AppData\\Local\\CORRECT"}),
                 encoding="utf-8-sig")  # Inno may write a BOM
    info = pp.install_info(f)
    assert info["scope"] == "user"


def test_install_info_missing_or_bad(tmp_path):
    assert pp.install_info(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert pp.install_info(bad) == {}


def test_dev_checkout_has_no_install_info():
    # Repo layout is <repo>/netrt_core, not <root>/app/netrt_core.
    assert pp.install_info() == {}
    assert pp.install_scope() is None


def test_data_root_follows_install_info(monkeypatch):
    monkeypatch.setattr(pp, "install_info", lambda path=None: {"scope": "user", "data_dir": "D:\\Data\\CORRECT"})
    monkeypatch.setattr(pp, "IS_WINDOWS", True)
    assert pp.install_scope() == "user"
    assert pp.windows_data_root() == "D:\\Data\\CORRECT"
    assert pp.default_config_path() == os.path.join("D:\\Data\\CORRECT", "config.yaml")


# ── first-run config ────────────────────────────────────────────────────────
def test_render_template_fills_paths_and_port():
    text = setup_tasks.render_template("C:\\Users\\o'brien\\AppData\\Local\\CORRECT", port=11999)
    cfg = yaml.safe_load(text)
    root = "C:\\Users\\o'brien\\AppData\\Local\\CORRECT"
    assert cfg["directories"]["working"] == root + "\\working"
    assert cfg["anonymization"]["pid_mapping_file"] == root + "\\shared\\pid_mapping.json"
    assert cfg["reporting"]["output_directory"] == root + "\\shared"
    assert cfg["dicom_listener"]["port"] == 11999
    assert cfg["dicom_destination"]["port"] == 104  # only the listener port changes
    assert "{DATA_DIR}" not in text


def test_init_config_creates_once(tmp_path):
    path, created = setup_tasks.init_config(str(tmp_path / "CORRECT"), scope=None)
    assert created and os.path.exists(path)
    for sub in setup_tasks.DATA_SUBDIRS:
        assert (tmp_path / "CORRECT" / sub).is_dir()
    with open(path, "a", encoding="utf-8") as f:
        f.write("# edited\n")
    path2, created2 = setup_tasks.init_config(str(tmp_path / "CORRECT"), scope=None)
    assert path2 == path and not created2
    assert open(path, encoding="utf-8").read().endswith("# edited\n")


def test_fresh_template_blocks_save_until_edited(tmp_path):
    path, _ = setup_tasks.init_config(str(tmp_path / "CORRECT"), scope=None)
    errors = ConfigModel(path).validate()
    assert "dicom_destination.ip" in errors  # YOUR_PACS_IP placeholder


def test_config_paths_from_template(tmp_path):
    data = tmp_path / "CORRECT"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "directories": {"working": str(data / "working"), "logs": str(data / "logs")},
        "reporting": {"output_directory": "\\\\fileserver\\share\\reports"},
        "anonymization": {"pid_mapping_file": str(data / "shared" / "pid.json")},
    }))
    writable, port = sc.config_paths(str(path))
    assert port == 11112
    assert str(data / "working") in writable and str(data / "shared") in writable
    assert not any(p.startswith("\\\\") for p in writable)  # UNC skipped
    assert len(writable) == 3


# ── elevation plumbing ──────────────────────────────────────────────────────
def test_elevated_command_line_never_contains_password():
    exe, params = elevated.build_command(
        ["install-service", "--scope", "user", "--user", "CONTOSO\\Jane Doe", "--prompt-password"],
        "C:\\Temp\\r.json")
    assert params.startswith("-m correct_admin.service_control --result-file C:\\Temp\\r.json install-service")
    assert '"CONTOSO\\Jane Doe"' in params
    assert "password" not in params.replace("--prompt-password", "")


def test_cli_parses_installer_uninstall_args():
    args = sc.build_parser().parse_args(["--elevate-if-needed", "remove-service"])
    assert args.cmd == "remove-service" and args.elevate_if_needed


def test_cli_init_config_and_result_file(tmp_path):
    result = tmp_path / "r.json"
    rc = sc.main(["--result-file", str(result), "init-config", "--data-dir", str(tmp_path / "D"), "--port", "12000"])
    assert rc == 0
    out = json.loads(result.read_text())
    assert out["ok"] and "created" in out["message"]
    cfg = yaml.safe_load((tmp_path / "D" / "config.yaml").read_text())
    assert cfg["dicom_listener"]["port"] == 12000


def test_cli_windows_only_commands_fail_cleanly_off_windows(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Windows-only behaviour")
    result = tmp_path / "r.json"
    rc = sc.main(["--result-file", str(result), "remove-service"])
    assert rc == 1
    assert "only available on Windows" in json.loads(result.read_text())["message"]

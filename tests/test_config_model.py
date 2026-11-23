"""Config editor model: comment-preserving round trip, defaults and validation."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from correct_admin.config_model import ConfigModel, SECTIONS, all_fields, validate_value  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def field(*path):
    return next(f for f in all_fields() if f.path == path)


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    with open(os.path.join(REPO, "config.yaml"), encoding="utf-8") as f:
        p.write_text(f.read())
    return p


def test_roundtrip_unchanged_preserves_file(cfg):
    original = cfg.read_text()
    m = ConfigModel(str(cfg))
    for f in all_fields():
        m.set(f, m.get(f))
    m.save()
    assert cfg.read_text() == original
    assert (cfg.parent / "config.yaml.bak").read_text() == original


def test_edit_keeps_comments_and_flow_lists(cfg):
    m = ConfigModel(str(cfg))
    m.set(field("dicom_destination", "ip"), "10.0.0.5")
    m.set(field("dicom_destination", "port"), "11113")
    m.set(field("processing", "target_contour_names_containing"), "GTV\nCTV\n")
    m.save()
    text = cfg.read_text()
    assert "# IP address of the remote DICOM destination" in text
    assert "ip: 10.0.0.5" in text
    assert "port: 11113" in text
    assert 'target_contour_names_containing: ["GTV", "CTV"]' in text or \
           "target_contour_names_containing: [GTV, CTV]" in text
    reloaded = ConfigModel(str(cfg))
    assert reloaded.get(field("dicom_destination", "port")) == 11113
    assert reloaded.get(field("processing", "target_contour_names_containing")) == ["GTV", "CTV"]


def test_missing_keys_use_defaults_and_are_not_written(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("dicom_listener:\n  port: 11112\n")
    m = ConfigModel(str(p))
    assert m.get(field("features", "create_gsps")) is True
    assert m.get(field("logging", "level")) == "INFO"
    for f in all_fields():
        m.set(f, m.get(f))
    m.save()
    assert p.read_text() == "dicom_listener:\n  port: 11112\n"


def test_new_file_created(tmp_path):
    p = tmp_path / "sub" / "config.yaml"
    m = ConfigModel(str(p))
    assert not m.exists
    m.set(field("dicom_listener", "ae_title"), "MYAE")
    m.save()
    assert "ae_title: MYAE" in p.read_text()


@pytest.mark.parametrize("path,value,ok", [
    (("dicom_listener", "port"), 11112, True),
    (("dicom_listener", "port"), 0, False),
    (("dicom_listener", "port"), "abc", False),
    (("dicom_listener", "ae_title"), "CORRECT", True),
    (("dicom_listener", "ae_title"), "X" * 17, False),
    (("dicom_listener", "ae_title"), "", False),
    (("dicom_destination", "ip"), "192.168.1.10", True),
    (("dicom_destination", "ip"), "pacs.example.org", True),
    (("dicom_destination", "ip"), "not a host!", False),
    (("directories", "working"), "/opt/correct/working", True),
    (("directories", "working"), "~/CORRECT_working", True),
    (("directories", "working"), "relative/dir", False),
    (("processing", "target_contour_names_containing"), [], False),
    (("anonymization", "rules", "blank_tags"), [], True),
    (("logging", "level"), "debug", True),
    (("logging", "level"), "LOUD", False),
])
def test_validation(path, value, ok):
    assert (validate_value(field(*path), value) is None) == ok


def test_save_refuses_invalid(cfg):
    m = ConfigModel(str(cfg))
    m.set(field("dicom_listener", "port"), "99999")
    with pytest.raises(ValueError):
        m.save()


def test_every_field_has_unique_key():
    keys = [f.key for fs in SECTIONS.values() for f in fs]
    assert len(keys) == len(set(keys))

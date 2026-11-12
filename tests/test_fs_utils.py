"""link_or_copy must work without symlink privileges (Windows service accounts)."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from netrt_core import fs_utils  # noqa: E402


def _src(tmp_path):
    src = tmp_path / "a.dcm"
    src.write_bytes(b"DICM-data")
    return src


def test_symlink_preferred(tmp_path):
    src = _src(tmp_path)
    dst = tmp_path / "b.dcm"
    method = fs_utils.link_or_copy(str(src), str(dst))
    # Windows only allows symlinks with SeCreateSymbolicLinkPrivilege / Developer Mode.
    assert method == "symlink" or (sys.platform == "win32" and method == "hardlink")
    assert dst.read_bytes() == b"DICM-data"


def test_falls_back_to_hardlink(tmp_path, monkeypatch):
    def no_symlink(*a, **k):
        raise OSError(1314, "A required privilege is not held by the client")
    monkeypatch.setattr(fs_utils.os, "symlink", no_symlink)
    src = _src(tmp_path)
    dst = tmp_path / "b.dcm"
    assert fs_utils.link_or_copy(str(src), str(dst)) == "hardlink"
    assert dst.read_bytes() == b"DICM-data"


def test_falls_back_to_copy(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise OSError("not allowed")
    monkeypatch.setattr(fs_utils.os, "symlink", fail)
    monkeypatch.setattr(fs_utils.os, "link", fail)
    src = _src(tmp_path)
    dst = tmp_path / "b.dcm"
    assert fs_utils.link_or_copy(str(src), str(dst)) == "copy"
    assert dst.read_bytes() == b"DICM-data"

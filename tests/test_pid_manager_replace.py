"""PID mapping saves must overwrite the existing file (os.rename fails on Windows)."""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from netrt_core import pid_manager as pm  # noqa: E402


def test_mapping_saved_repeatedly(tmp_path, monkeypatch):
    # Simulate Windows semantics: rename onto an existing file raises.
    real_rename = os.rename

    def windows_rename(src, dst):
        if os.path.exists(dst):
            raise FileExistsError(183, "Cannot create a file when that file already exists")
        return real_rename(src, dst)
    monkeypatch.setattr(pm.os, "rename", windows_rename)

    mapping_file = tmp_path / "shared" / "pid_mapping.json"
    mgr = pm.PIDManager({"site_code": "TST", "pid_mapping_file": str(mapping_file)})
    first = mgr.get_anonymized_id("P1", "DOE^JOHN", "20240101")
    second = mgr.get_anonymized_id("P2", "DOE^JANE", "20240102")

    saved = json.loads(mapping_file.read_text())
    assert {v["anonymized_id"] for v in saved.values()} == {first, second}
    assert not (tmp_path / "shared" / "pid_mapping.json.tmp").exists()

"""CorrectApp starts without blocking, answers C-ECHO, and stops cleanly (service stop path)."""

import os
import socket
import sys
import threading

import logging

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pynetdicom import AE  # noqa: E402
from pynetdicom.sop_class import Verification  # noqa: E402

from netrt_core.app import CorrectApp  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_logging():
    """setup_logging() mutates global logging state; undo it so later tests are unaffected."""
    root = logging.getLogger()
    txn = logging.getLogger("transaction")
    saved = (list(root.handlers), root.level, list(txn.handlers), txn.level, txn.propagate)
    yield
    for h in root.handlers[:]:
        if h not in saved[0]:
            root.removeHandler(h)
            h.close()
    root.setLevel(saved[1])
    for h in txn.handlers[:]:
        if h not in saved[2]:
            txn.removeHandler(h)
            h.close()
    txn.setLevel(saved[3])
    txn.propagate = saved[4]
    logging.captureWarnings(False)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_start_echo_stop(tmp_path):
    port = _free_port()
    cfg = {
        "dicom_listener": {"host": "127.0.0.1", "port": port, "ae_title": "CORRECT"},
        "directories": {"working": str(tmp_path / "working"), "logs": str(tmp_path / "logs")},
        "anonymization": {"pid_mapping_file": str(tmp_path / "shared" / "pid_mapping.json")},
        "reporting": {"output_directory": str(tmp_path / "shared")},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    app = CorrectApp(str(cfg_path))
    app.start()
    try:
        assert app.bound_port == port
        ae = AE(ae_title="TESTSCU")
        ae.add_requested_context(Verification)
        assoc = ae.associate("127.0.0.1", port, ae_title="CORRECT")
        assert assoc.is_established
        assert assoc.send_c_echo().Status == 0x0000
        assoc.release()
    finally:
        app.stop()

    app.stop()  # idempotent
    assert not app.file_system_manager.observer.is_alive()
    with socket.socket() as s:  # listener closed: connection refused
        s.settimeout(2)
        with pytest.raises(OSError):
            s.connect(("127.0.0.1", port))
    assert (tmp_path / "logs" / "application.log").exists()


def test_wait_returns_when_event_set(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "dicom_listener": {"host": "127.0.0.1", "port": _free_port()},
        "directories": {"working": str(tmp_path / "w"), "logs": str(tmp_path / "l")},
        "anonymization": {"pid_mapping_file": str(tmp_path / "s" / "p.json")},
        "reporting": {"output_directory": str(tmp_path / "s")},
    }))
    app = CorrectApp(str(cfg_path))
    app.start()
    ev = threading.Event()
    t = threading.Thread(target=app.wait, args=(ev, 0.05))
    t.start()
    ev.set()
    t.join(10)
    assert not t.is_alive()
    assert app._stopped.is_set()

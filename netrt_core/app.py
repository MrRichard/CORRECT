"""Application runtime shared by the console entry point, systemd and the Windows service.

``CorrectApp`` wires up the same components main.py always has, but starts the DICOM
listener without blocking and exposes ``stop()`` so a service controller (SCM, systemd
SIGTERM, Ctrl+C) can shut everything down cleanly.
"""

import logging
import os
import threading

from .config_loader import load_config
from .logging_setup import setup_logging
from .file_system_manager import FileSystemManager
from .dicom_listener import DicomListener
from .study_processor import StudyProcessor

logger = logging.getLogger(__name__)


class CorrectApp:
    """Owns the CORRECT components and their lifecycle."""

    def __init__(self, config_path, debug=False, config_overrides=None):
        self.config_path = config_path
        self.debug = debug
        self.config_overrides = config_overrides or {}
        self.config = None
        self.dicom_listener = None
        self.file_system_manager = None
        self.study_processor = None
        self.server = None
        self._stopped = threading.Event()
        self._stop_lock = threading.Lock()
        self._started = False

    def start(self):
        """Load config, set up logging and start the watcher + DICOM listener (non-blocking)."""
        config = load_config(self.config_path)
        config["debug_mode"] = self.debug
        for section, values in self.config_overrides.items():
            if isinstance(values, dict):
                config.setdefault(section, {}).update(values)
            else:
                config[section] = values
        self.config = config

        setup_logging(config)
        logger.info("NETRT Application starting...")
        logger.info(f"Using configuration file: {os.path.abspath(self.config_path)}")
        logger.debug(f"Loaded configuration: {config}")
        if config["debug_mode"]:
            logger.info(f"DEBUG mode is {config['debug_mode']}")

        self.study_processor = StudyProcessor(config, None)
        self.file_system_manager = FileSystemManager(config, self.study_processor.process_study)
        self.study_processor.fsm = self.file_system_manager

        listener_cfg = config.get("dicom_listener", {})
        self.dicom_listener = DicomListener(
            host=listener_cfg.get("host", "0.0.0.0"),
            port=listener_cfg.get("port", 11112),
            ae_title=listener_cfg.get("ae_title", "CORRECT"),
            study_processor_callback=None,
            file_system_manager=self.file_system_manager,
            config=config,
        )

        def study_processing_callback(study_instance_uid):
            sender_info = self.dicom_listener.study_senders.get(study_instance_uid, {})
            self.study_processor.process_study(study_instance_uid, sender_info)

        self.file_system_manager.study_processor_callback = study_processing_callback
        self.file_system_manager.start_watching()

        logger.info(
            f"Starting DICOM listener on {self.dicom_listener.host}:{self.dicom_listener.port} "
            f"AE: {self.dicom_listener.ae_title}"
        )
        try:
            self.server = self.dicom_listener.start(block=False)
        except Exception:
            self.file_system_manager.stop_watching()
            raise
        self._started = True

    @property
    def bound_port(self):
        """The TCP port the listener is actually bound to (useful when port 0 is configured)."""
        if self.server is None:
            return None
        return self.server.server_address[1]

    def stop(self):
        """Stop the listener and watcher. Safe to call more than once or from any thread."""
        with self._stop_lock:
            if self._stopped.is_set():
                return
            logger.info("Stopping services...")
            try:
                if self.dicom_listener is not None:
                    self.dicom_listener.stop()
            except Exception as e:
                logger.error(f"Error stopping DICOM listener: {e}", exc_info=True)
            try:
                if self.file_system_manager is not None:
                    self.file_system_manager.stop_watching()
            except Exception as e:
                logger.error(f"Error stopping file watcher: {e}", exc_info=True)
            self._stopped.set()
            logger.info("NETRT Application shut down.")

    def wait(self, stop_event, poll_interval=1.0):
        """Block until ``stop_event`` is set, then stop the app."""
        try:
            while not stop_event.wait(poll_interval):
                pass
        finally:
            self.stop()

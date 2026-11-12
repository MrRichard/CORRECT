import os
import sys
import signal
import logging
import argparse
import threading

# Adjust path to import from netrt_core, assuming main.py is in the NETRT root directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from netrt_core.app import CorrectApp


logger = logging.getLogger(__name__) # Main application logger


def _install_signal_handlers(stop_event):
    """Turn SIGINT/SIGTERM (and SIGBREAK on Windows) into a clean shutdown."""
    def _handler(signum, _frame):
        logger.info(f"Received signal {signum}. Shutting down...")
        stop_event.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass  # not in main thread / not supported on this platform


def main():
    parser = argparse.ArgumentParser(description="NETRT DICOM Processing Application")
    parser.add_argument("--config", default="config.yaml", help="Path to the configuration file (default: config.yaml)")
    parser.add_argument("--debug", action="store_true", help="Enable debug visualization output")
    args = parser.parse_args()

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    app = CorrectApp(args.config, debug=args.debug)
    try:
        app.start()
    except Exception as e:
        logger.error(f"An unhandled exception occurred while starting: {e}", exc_info=True)
        app.stop()
        sys.exit(1)

    try:
        app.wait(stop_event)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down...")
        app.stop()

if __name__ == "__main__":
    main()

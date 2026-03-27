import os
import sys
import logging
import argparse

# Adjust path to import from netrt_core, assuming main.py is in the NETRT root directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from netrt_core.config_loader import load_config
from netrt_core.logging_setup import setup_logging, TRANSACTION_LOGGER_NAME
from netrt_core.file_system_manager import FileSystemManager
from netrt_core.dicom_listener import DicomListener
from netrt_core.study_processor import StudyProcessor


logger = logging.getLogger(__name__) # Main application logger
transaction_logger = logging.getLogger(TRANSACTION_LOGGER_NAME)

def main():
    parser = argparse.ArgumentParser(description="NETRT DICOM Processing Application")
    parser.add_argument("--config", default="config.yaml", help="Path to the configuration file (default: config.yaml)")
    parser.add_argument("--debug", action="store_true", help="Enable debug visualization output") 
    args = parser.parse_args()

    # 1. Load Configuration
    config = load_config(args.config)
    config['debug_mode'] = args.debug

    # 2. Setup Logging (must be done after config is loaded)
    setup_logging(config)

    logger.info("NETRT Application starting...")
    logger.info(f"Using configuration file: {os.path.abspath(args.config)}")
    logger.debug(f"Loaded configuration: {config}")
    if config['debug_mode']:
        logger.info(f"DEBUG mode is {config['debug_mode']}")

    # 3. Initialize Core Components
    # The study_processor_callback will be passed to FileSystemManager, 
    # which in turn passes it to its NewStudyEventHandler.
    # This callback needs to be a method of an initialized StudyProcessor instance.
    
    study_processor_instance = StudyProcessor(config, None) # FSM not strictly needed by SP constructor for now

    file_system_manager = FileSystemManager(config, study_processor_instance.process_study)
    study_processor_instance.fsm = file_system_manager # Now link FSM to StudyProcessor
    
    dicom_listener = DicomListener(
        host=config.get("dicom_listener", {}).get("host", "0.0.0.0"),
        port=config.get("dicom_listener", {}).get("port", 11112),
        ae_title=config.get("dicom_listener", {}).get("ae_title", "CORRECT"),
        study_processor_callback=None, # No longer directly used
        file_system_manager=file_system_manager,
        config=config,
    )

    def study_processing_callback(study_instance_uid):
        sender_info = dicom_listener.study_senders.get(study_instance_uid, {})
        study_processor_instance.process_study(study_instance_uid, sender_info)

    file_system_manager.study_processor_callback = study_processing_callback


    # 4. Start Services
    # Start file system watcher first if it runs in a separate thread
    file_system_manager.start_watching() 

    try:
        logger.info(f"Starting DICOM listener on {dicom_listener.host}:{dicom_listener.port} AE: {dicom_listener.ae_title}")
        dicom_listener.start() # This is expected to block
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down...")
    except Exception as e:
        logger.error(f"An unhandled exception occurred in the main loop: {e}", exc_info=True)
    finally:
        logger.info("Stopping services...")
        if hasattr(dicom_listener, "stop") and callable(dicom_listener.stop):
            dicom_listener.stop()
        if hasattr(file_system_manager, "stop_watching") and callable(file_system_manager.stop_watching):
            file_system_manager.stop_watching()
        logger.info("NETRT Application shut down.")

if __name__ == "__main__":
    main()

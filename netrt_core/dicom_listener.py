import logging
from pydicom.filewriter import write_file_meta_info
from pynetdicom import AE, evt, AllStoragePresentationContexts, ALL_TRANSFER_SYNTAXES
from pynetdicom.sop_class import Verification

# Standard logger for general module events
logger = logging.getLogger(__name__)
# Specific logger for transaction events, configured in logging_setup.py
transaction_logger = logging.getLogger("transaction")

class DicomListener:
    """Handles DICOM C-STORE server setup and events."""

    def __init__(self, host, port, ae_title, study_processor_callback, file_system_manager, config=None):
        """Initialize the DICOM listener.

        Args:
            host (str): The IP address to listen on.
            port (int): The port number to listen on.
            ae_title (str): The AE title of this server.
            study_processor_callback (callable): Callback function to trigger study processing.
            file_system_manager (FileSystemManager): Instance of the FileSystemManager.
            config (dict, optional): Application configuration. Defaults to None.
        """
        self.host = host
        self.port = port
        self.ae_title = ae_title
        self.study_processor_callback = study_processor_callback
        self.file_system_manager = file_system_manager
        self.config = config if config is not None else {}
        self.ae = AE(ae_title=self.ae_title)
        self.study_senders = {}
        self._configure_ae()

    def _configure_ae(self):
        """Configure supported presentation contexts for the AE."""
        self.ae.add_supported_context(Verification)
        # Use all standard storage SOP classes
        for context in AllStoragePresentationContexts:
            self.ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)
        # Add any specific SOP classes if needed, though AllStoragePresentationContexts is comprehensive

    def _handle_association_accepted(self, event):
        """Handle an association accepted event."""
        source_ip = event.assoc.requestor.address
        source_ae_title = event.assoc.requestor.ae_title
        transaction_logger.info(f"DICOM association accepted. SourceIP: {source_ip}, SourceAET: {source_ae_title}")
        logger.info(f"Association accepted from {source_ae_title}@{source_ip}")
        return 0x0000

    def _handle_echo(self, event):
        """Handle a C-ECHO request event."""
        source_ip = event.assoc.requestor.address
        logger.info(f"C-ECHO request received from {source_ip}")
        transaction_logger.info(f"C-ECHO received. SourceIP: {source_ip}")
        return 0x0000  # Success

    def _handle_store(self, event):
        """Handle a C-STORE request event."""
        try:
            dataset = event.dataset

            listener_config = self.config.get("dicom_listener", {})
            use_negotiated_ts = listener_config.get("config_negotiated_transfer_syntax", True)

            # Extract the negotiated transfer syntax if configured and available
            negotiated_ts = None
            if use_negotiated_ts and hasattr(event, 'context') and event.context:
                negotiated_ts = event.context.transfer_syntax
            
            # Safely log the transfer syntax
            if negotiated_ts:
                logger.debug(f"Using negotiated transfer syntax for saving: {negotiated_ts}")
            else:
                logger.debug("No negotiated transfer syntax available or configured; will use file meta/default")

            # Ensure StudyInstanceUID is present
            if not hasattr(dataset, "StudyInstanceUID") or not dataset.StudyInstanceUID:
                logger.error("Received dataset missing StudyInstanceUID. Rejecting store.")
                return 0xA700  # Out of Resources - Or a more specific error

            study_instance_uid = dataset.StudyInstanceUID
            if study_instance_uid not in self.study_senders:
                self.study_senders[study_instance_uid] = {
                    "ip": event.assoc.requestor.address,
                    "ae_title": event.assoc.requestor.ae_title,
                }

            # Use FileSystemManager to get the path for storing the file
            file_path = self.file_system_manager.save_incoming_dicom(
                dataset, 
                event.file_meta,
                negotiated_ts  # Pass the transfer syntax object or None
            )

            if file_path:
                logger.info(f"Stored DICOM file: {file_path} from {event.assoc.requestor.address}")
            else:
                logger.error("Failed to save DICOM file.")
                return 0xA700  # Or another appropriate error status

        except Exception as e:
            logger.error(f"Error handling C-STORE request: {e}", exc_info=True)
            return 0xC001  # Processing failure

        return 0x0000  # Success

    def _handle_conn_close(self, event):
        """Handle a connection close event."""
        source_ip = event.assoc.requestor.address
        source_ae_title = event.assoc.requestor.ae_title
        logger.info(f"Connection closed with {source_ae_title}@{source_ip}.")
        transaction_logger.info(f"DICOM association released/closed. SourceIP: {source_ip}, SourceAET: {source_ae_title}")
        # The FileSystemManager with watchdog will handle study completion detection based on file activity and debounce.
        # No explicit call to study_processor_callback here is needed if watchdog is primary mechanism.
        return 0x0000

    def start(self):
        """Start the DICOM listener server."""
        handlers = [
            (evt.EVT_ACCEPTED, self._handle_association_accepted),
            (evt.EVT_C_ECHO, self._handle_echo),
            (evt.EVT_C_STORE, self._handle_store),
            (evt.EVT_RELEASED, self._handle_conn_close), # EVT_RELEASED is for graceful release
            (evt.EVT_ABORTED, self._handle_conn_close), # Also log if aborted
            (evt.EVT_CONN_CLOSE, self._handle_conn_close) # General connection close
        ]

        logger.info(f"Starting DICOM server on {self.host}:{self.port} with AE Title: {self.ae_title}")
        try:
            self.ae.start_server((self.host, self.port), evt_handlers=handlers, block=True)
        except OSError as e:
            logger.error(f"Failed to start DICOM server on {self.host}:{self.port} - {e}. Check if port is already in use or address is valid.")
            # Potentially re-raise or handle more gracefully depending on main app structure
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred while starting or running the DICOM server: {e}", exc_info=True)
            raise

    def stop(self):
        """Stop the DICOM listener server."""
        logger.info("Stopping DICOM server...")
        if hasattr(self.ae, "shutdown") and callable(self.ae.shutdown):
            self.ae.shutdown()
        logger.info("DICOM server stopped.")

import os
import logging
import time
import pydicom

from .dicom_sender import DicomSender
from .burn_in_processor import BurnInProcessor
from .contour_processor import ContourProcessor
from .gsps_processor import GSPSProcessor
from .segmentation_processor import SegmentationProcessor
from .report_generator import ReportGenerator
from DicomAnonymizer import DicomAnonymizer
import datetime

logger = logging.getLogger(__name__)
transaction_logger = logging.getLogger("transaction")

class StudyProcessor:
    """Orchestrates the processing pipeline for a received DICOM study."""

    def __init__(self, config, file_system_manager):
        self.config = config
        self.fsm = file_system_manager
        self.anonymizer = DicomAnonymizer(self.config.get("anonymization", {}))
        self.contour_processor = ContourProcessor(self.config)
        self.gsps_processor = GSPSProcessor(self.config)
        self.burn_in_processor = BurnInProcessor(self.config.get("processing", {}).get("burn_in_text"))

    def process_study(self, study_instance_uid, sender_info=None):
        """Main entry point for processing a study."""
        if sender_info is None:
            sender_info = {}
        report = ReportGenerator(self.config, study_instance_uid, sender_info)
        study_path = self.fsm.get_study_path(study_instance_uid)
        processing_start_time = time.time()
        transaction_logger.info(f"PROCESSING_START StudyUID: {study_instance_uid}, Path: {study_path}")
        logger.info(f"Starting processing for study: {study_instance_uid}")
        report.add_line(f"Processing started at: {datetime.datetime.fromtimestamp(processing_start_time)}")

        try:
            dcm_path, struct_path, addition_path = self._setup_paths(study_path)

            num_dicom_files = len([f for f in os.listdir(dcm_path) if f.lower().endswith('.dcm')])
            report.add_line(f"Number of DICOM files received: {num_dicom_files}")

            if not self._validate_inputs(dcm_path, study_instance_uid):
                report.add_error("Input validation failed: Missing or empty DCM directory")
                report.write_report()
                return False

            struct_file = self._find_struct_file(struct_path)
            report.add_line(f"RTSTRUCT file found: {struct_file}")

            if self.config.get("anonymization", {}).get("enabled", True):
                report.add_line("Anonymization enabled. Anonymizing study...")
                self._anonymize_study(dcm_path, struct_file)
                report.add_line("Anonymization complete.")

            if struct_file:
                report.add_line("Contour processing started...")
                burn_in_text = self.config.get("processing", {}).get("burn_in_text")
                success, debug_dicom_dir = self.contour_processor.run(
                    dcm_path, struct_file, addition_path,
                    study_instance_uid,
                    burn_in_text=burn_in_text
                )
                if not success:
                    raise Exception("Contour processing failed")
                report.add_line("Contour processing successful.")

                # Create GSPS alongside the overlay series
                try:
                    overlay_files = [
                        pydicom.dcmread(os.path.join(addition_path, f))
                        for f in os.listdir(addition_path)
                        if f.lower().startswith("overlay-") and f.lower().endswith(".dcm")
                    ]
                    if overlay_files:
                        gsps_path = self.gsps_processor.create_gsps(overlay_files, addition_path)
                        report.add_line(f"GSPS file created: {os.path.basename(gsps_path)}")
                    else:
                        logger.warning("No overlay files found for GSPS creation.")
                except Exception as e:
                    logger.warning(f"GSPS creation failed (non-critical): {e}", exc_info=True)
                    report.add_line(f"WARNING: GSPS creation failed: {e}")

                # Create DICOM SEG files if enabled
                if self.config.get("feature_flags", {}).get("enable_segmentation_export", False):
                    try:
                        seg_processor = SegmentationProcessor(self.config)
                        seg_files = seg_processor.create_segmentations(struct_file, dcm_path, addition_path)
                        report.add_line(f"DICOM SEG: created {len(seg_files)} segmentation file(s).")
                    except Exception as e:
                        logger.warning(f"SEG creation failed (non-critical): {e}", exc_info=True)
                        report.add_line(f"WARNING: SEG creation failed: {e}")

                if self.config.get("processing", {}).get("add_burn_in_disclaimer", True):
                    report.add_line("Adding burn-in disclaimer...")
                    self.burn_in_processor.run(addition_path)
                    report.add_line("Burn-in disclaimer added.")

                report.add_line("Sending processed series...")
                send_success = self._send_directory(addition_path, "OVERLAY", study_instance_uid)
                if send_success:
                    report.add_line("Processed series sent successfully.")
                else:
                    report.add_line("ERROR: Failed to send processed series to destination.")
                    raise Exception(f"Failed to send overlay series to destination PACS")

                # Send debug DICOM series if created
                if debug_dicom_dir and os.path.exists(debug_dicom_dir):
                    report.add_line("Sending debug series...")
                    debug_send_success = self._send_directory(debug_dicom_dir, "DEBUG", study_instance_uid)
                    if debug_send_success:
                        report.add_line("Debug series sent successfully.")
                    else:
                        report.add_line("WARNING: Failed to send debug series to destination (non-critical).")
            else:
                logger.warning(f"No RTSTRUCT file found for study {study_instance_uid}. Nothing to process or send.")
                report.add_line("No RTSTRUCT file found. No processing performed.")

            self.fsm.cleanup_study_directory(study_instance_uid)
            processing_duration = time.time() - processing_start_time
            logger.info(f"Processing for study {study_instance_uid} completed successfully in {processing_duration:.2f} seconds.")
            transaction_logger.info(f"PROCESSING_SUCCESS StudyUID: {study_instance_uid}, DurationSec: {processing_duration:.2f}")
            report.add_line(f"\nProcessing successful in {processing_duration:.2f} seconds.")
            report.write_report()
            return True

        except Exception as e:
            processing_duration = time.time() - processing_start_time
            logger.error(f"Error processing study {study_instance_uid}: {e}", exc_info=True)
            self.fsm.quarantine_study(study_instance_uid, str(e))
            transaction_logger.error(f"PROCESSING_FAILED StudyUID: {study_instance_uid}, DurationSec: {processing_duration:.2f}, Reason: {str(e)}")
            report.add_error(e)
            report.write_report()
            return False

    def _setup_paths(self, study_path):
        """Create and return the necessary directory paths for processing."""
        dcm_path = os.path.join(study_path, "DCM")
        struct_path = os.path.join(study_path, "Structure")
        addition_path = os.path.join(study_path, "Addition")
        os.makedirs(addition_path, exist_ok=True)
        return dcm_path, struct_path, addition_path

    def _validate_inputs(self, dcm_path, study_instance_uid):
        """Validates that the necessary input directories and files exist."""
        if not os.path.isdir(dcm_path) or not os.listdir(dcm_path):
            logger.error(f"DCM directory is missing or empty for study {study_instance_uid}")
            self.fsm.quarantine_study(study_instance_uid, "Missing or empty DCM directory")
            return False
        return True

    def _find_struct_file(self, struct_dir_path):
        """Finds the first DICOM file in the Structure directory."""
        if not os.path.isdir(struct_dir_path) or not os.listdir(struct_dir_path):
            return None
        
        struct_files = [os.path.join(struct_dir_path, f) for f in os.listdir(struct_dir_path) if f.lower().endswith(".dcm")]
        if not struct_files:
            return None
        
        if len(struct_files) > 1:
            logger.warning(f"Multiple RTSTRUCT files found. Using the first one: {struct_files[0]}")
        return struct_files[0]

    def _anonymize_study(self, dcm_path, struct_file_path):
        """Anonymizes all DICOM files in a study."""
        logger.info(f"Anonymizing files in {dcm_path}...")
        for root, _, files in os.walk(dcm_path):
            for filename in files:
                if filename.lower().endswith(".dcm"):
                    self.anonymizer.anonymize_file(os.path.join(root, filename))
        
        if struct_file_path:
            logger.info(f"Anonymizing RTSTRUCT file: {struct_file_path}")
            self.anonymizer.anonymize_file(struct_file_path)

    def _send_directory(self, directory_path, series_type, study_instance_uid):
        """Sends a directory of DICOM files to the configured destination.

        Returns:
            bool: True if sending was successful, False otherwise.
        """
        dest_config = self.config.get("dicom_destination", {})
        sender = DicomSender(dest_config.get("ip"), dest_config.get("port"), dest_config.get("ae_title"))

        transaction_logger.info(f"SENDING_START SeriesType: {series_type}, StudyUID: {study_instance_uid}, DestAET: {sender.ae_title}")
        success = sender.send_directory(directory_path)

        if success:
            transaction_logger.info(f"SENDING_SUCCESS SeriesType: {series_type}, StudyUID: {study_instance_uid}, DestAET: {sender.ae_title}")
        else:
            transaction_logger.error(f"SENDING_FAILED SeriesType: {series_type}, StudyUID: {study_instance_uid}, DestAET: {sender.ae_title}")
            logger.error(f"Failed to send {series_type} series to {sender.ae_title}@{dest_config.get('ip')}:{dest_config.get('port')}")

        return success

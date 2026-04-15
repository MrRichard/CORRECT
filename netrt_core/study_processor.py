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

            struct_file = self._find_struct_file(struct_path, dcm_path)
            report.add_line(f"RTSTRUCT file found: {struct_file}")

            anon_cfg = self.config.get("anonymization", {})
            if anon_cfg.get("apply_site_code", False) or anon_cfg.get("apply_anonymization_rules", False):
                report.add_line("Anonymization enabled. Anonymizing study...")
                self._anonymize_study(dcm_path, struct_file)
                report.add_line("Anonymization complete.")

            if struct_file:
                report.add_line("Contour processing started...")
                burn_in_text = self.config.get("processing", {}).get("burn_in_text")
                success, sc_dicom_dir = self.contour_processor.run(
                    dcm_path, struct_file, addition_path,
                    study_instance_uid,
                    burn_in_text=burn_in_text
                )
                if not success:
                    raise Exception("Contour processing failed")
                report.add_line("Contour processing successful.")

                # Create GSPS alongside the overlay series
                if self._feature_enabled("create_gsps", True):
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
                if self._feature_enabled("create_segmentation_export", True):
                    try:
                        seg_processor = SegmentationProcessor(self.config)
                        seg_files = seg_processor.create_segmentations(struct_file, dcm_path, addition_path)
                        report.add_line(f"DICOM SEG: created {len(seg_files)} segmentation file(s).")
                    except Exception as e:
                        logger.warning(f"SEG creation failed (non-critical): {e}", exc_info=True)
                        report.add_line(f"WARNING: SEG creation failed: {e}")

                if self._feature_enabled("create_augmented_series", True) and self.config.get("processing", {}).get("add_burn_in_disclaimer", True):
                    report.add_line("Adding burn-in disclaimer...")
                    self.burn_in_processor.run(addition_path)
                    report.add_line("Burn-in disclaimer added.")

                # Optionally send the original (unmodified) CT series
                if self._feature_enabled("send_original_series", True):
                    report.add_line("Sending original series...")
                    orig_success = self._send_directory(dcm_path, "ORIGINAL", study_instance_uid)
                    if orig_success:
                        report.add_line("Original series sent successfully.")
                    else:
                        report.add_line("WARNING: Failed to send original series (non-critical).")

                if self._feature_enabled("create_augmented_series", True):
                    report.add_line("Sending overlay series...")
                    send_success = self._send_directory(addition_path, "OVERLAY", study_instance_uid)
                    if send_success:
                        report.add_line("Overlay series sent successfully.")
                    else:
                        report.add_line("ERROR: Failed to send overlay series to destination.")
                        raise Exception(f"Failed to send overlay series to destination PACS")

                # Send Secondary Capture DICOM series if created
                if sc_dicom_dir and os.path.exists(sc_dicom_dir):
                    report.add_line("Sending SC series...")
                    sc_send_success = self._send_directory(sc_dicom_dir, "SC", study_instance_uid)
                    if sc_send_success:
                        report.add_line("SC series sent successfully.")
                    else:
                        report.add_line("WARNING: Failed to send SC series to destination (non-critical).")
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
            # A directory with only an RTSTRUCT (no CT) is expected when a TPS exports
            # the structure set under a different StudyInstanceUID. The CT study processor
            # finds it via cross-study FrameOfReferenceUID search; this directory itself
            # has nothing to process.
            logger.info(
                f"Study {study_instance_uid} has no DCM directory — likely an RTSTRUCT-only "
                f"study that will be claimed by its paired CT study. Skipping."
            )
            self.fsm.cleanup_study_directory(study_instance_uid)
            return False
        return True

    def _find_struct_file(self, struct_dir_path, dcm_path=None):
        """Finds the RTSTRUCT file for this study.

        First checks the study's own Structure directory. If nothing is found there,
        scans all other study directories in the working folder for an RTSTRUCT whose
        FrameOfReferenceUID matches the CT series. This handles the common case where
        a TPS exports the RTSTRUCT under a different StudyInstanceUID than the CT.
        """
        # --- Local Structure directory ---
        local = self._collect_struct_files(struct_dir_path)
        if local:
            if len(local) > 1:
                logger.warning(f"Multiple RTSTRUCT files found. Using the first one: {local[0]}")
            return local[0]

        # --- Cross-study search by FrameOfReferenceUID ---
        if dcm_path is None or not os.path.isdir(dcm_path):
            return None

        ct_for_uid = self._get_frame_of_reference_uid(dcm_path)
        if not ct_for_uid:
            logger.debug("Could not determine FrameOfReferenceUID from CT images; skipping cross-study RTSTRUCT search.")
            return None

        logger.info(
            f"No local RTSTRUCT found. Searching other study directories for an RTSTRUCT "
            f"with FrameOfReferenceUID={ct_for_uid}"
        )

        for entry in os.listdir(self.fsm.working_dir):
            candidate_study_path = os.path.join(self.fsm.working_dir, entry)
            if not entry.startswith("UID_") or candidate_study_path == os.path.dirname(struct_dir_path):
                continue
            candidate_struct_dir = os.path.join(candidate_study_path, "Structure")
            candidates = self._collect_struct_files(candidate_struct_dir)
            for candidate in candidates:
                try:
                    ds = pydicom.dcmread(candidate, stop_before_pixels=True)
                    rtstruct_for = getattr(ds, "FrameOfReferenceUID", None)
                    if rtstruct_for == ct_for_uid:
                        logger.info(f"Found cross-study RTSTRUCT matching FrameOfReferenceUID: {candidate}")
                        return candidate
                except Exception as e:
                    logger.debug(f"Could not read candidate RTSTRUCT {candidate}: {e}")

        return None

    def _collect_struct_files(self, struct_dir_path):
        """Returns a list of .dcm paths in a Structure directory, or [] if absent."""
        if not os.path.isdir(struct_dir_path) or not os.listdir(struct_dir_path):
            return []
        return [os.path.join(struct_dir_path, f) for f in os.listdir(struct_dir_path) if f.lower().endswith(".dcm")]

    def _get_frame_of_reference_uid(self, dcm_path):
        """Returns the FrameOfReferenceUID from the first readable CT file that has it."""
        for filename in os.listdir(dcm_path):
            if not filename.lower().endswith(".dcm"):
                continue
            try:
                ds = pydicom.dcmread(os.path.join(dcm_path, filename), stop_before_pixels=True)
                for_uid = getattr(ds, "FrameOfReferenceUID", None)
                if for_uid:
                    return str(for_uid)
            except Exception:
                continue
        return None

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

    def _feature_enabled(self, feature_name, default=True):
        """Return a normalized feature flag value with backwards-compatible fallbacks."""
        features = self.config.get("features", {})
        return features.get(feature_name, default)

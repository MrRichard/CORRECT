import hashlib
import logging
import pydicom
import tempfile
import os
import shutil
from netrt_core.pid_manager import PIDManager

logger = logging.getLogger(__name__)

class DicomAnonymizer:
    """
    A class to anonymize DICOM files according to NEMA standards while preserving
    image viewing capabilities.
    """
    
    def __init__(self, anonymization_config=None):
        """
        Initialize the DicomAnonymizer with the provided configuration.
        
        Args:
            anonymization_config (dict): Configuration for anonymization settings
        """
        self.config = anonymization_config or {}
        self.pid_manager = PIDManager(self.config)

        self.apply_site_code = self.config.get("apply_site_code", False)
        self.apply_anonymization_rules = self.config.get("apply_anonymization_rules", False)

        self.tags_to_remove = self.config.get("rules", {}).get("remove_tags", [])
        self.tags_to_empty = self.config.get("rules", {}).get("blank_tags", [])
        
        logger.debug(f"DicomAnonymizer initialized with config: {self.config}")
        logger.debug(f"Tags to remove: {self.tags_to_remove}")
        logger.debug(f"Tags to empty: {self.tags_to_empty}")

    def anonymize(self, dicom_obj):
        """
        Anonymize a DICOM object according to the configured rules.
        
        Args:
            dicom_obj: A pydicom.dataset.FileDataset object
            
        Returns:
            pydicom.dataset.FileDataset: Anonymized DICOM object
        """
        if not isinstance(dicom_obj, pydicom.dataset.Dataset):
            logger.error("Object provided for anonymization is not a pydicom Dataset")
            return dicom_obj

        if self.apply_site_code:
            original_patient_id = getattr(dicom_obj, 'PatientID', '')
            original_patient_name = str(getattr(dicom_obj, 'PatientName', ''))
            study_date = getattr(dicom_obj, 'StudyDate', None)

            anonymized_id = self.pid_manager.get_anonymized_id(
                original_patient_id,
                original_patient_name,
                study_date
            )

            dicom_obj.PatientID = anonymized_id
            dicom_obj.PatientName = anonymized_id

            if "study_description" in self.config:
                dicom_obj.StudyDescription = self.config["study_description"]

            logger.debug(f"Applied consistent anonymized ID: {anonymized_id}")

        if self.apply_anonymization_rules:
            protected = {'PatientID', 'PatientName', 'StudyDescription'} if self.apply_site_code else {'StudyDescription'}

            for tag in self.tags_to_remove:
                if tag in protected:
                    continue
                if hasattr(dicom_obj, tag):
                    delattr(dicom_obj, tag)

            for tag in self.tags_to_empty:
                if tag in protected:
                    continue
                if hasattr(dicom_obj, tag):
                    setattr(dicom_obj, tag, '')

        return dicom_obj

    def anonymize_file(self, filepath):
        """Anonymize a single DICOM file in place, using a temporary file for safety."""
        try:
            ds = pydicom.dcmread(filepath)
            anonymized_ds = self.anonymize(ds)

            temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), prefix=".tmp-")
            anonymized_ds.save_as(temp_path, enforce_file_format=True)
            os.close(temp_fd)

            shutil.move(temp_path, filepath)
            logger.debug(f"Successfully anonymized and replaced {filepath}")
        except Exception as e:
            logger.error(f"Failed to anonymize file {filepath}: {e}", exc_info=True)
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    
    def _generate_patient_id(self):
        """Generate a random patient ID for full anonymization"""
        import uuid
        # Generate a short unique identifier (8 characters)
        return str(uuid.uuid4()).replace('-', '')[:8]

    def _handle_date(self, date_str):
        """Modify date while preserving year/month"""
        if not date_str:
            return ''
        try:
            # Keep year and month, set day to 01
            return date_str[:6] + '01'
        except:
            return ''

    def _handle_time(self, time_str):
        """Modify time while preserving hour"""
        if not time_str:
            return ''
        try:
            # Keep hour, zero out minutes and seconds
            return time_str[:2] + '0000.000'
        except:
            return ''

    def _generate_uid(self, original_uid):
        """
        Generate a new UID based on the original one to maintain consistency
        while ensuring uniqueness
        """
        # Create a hash of the original UID
        hash_obj = hashlib.sha256(original_uid.encode())
        hashed = hash_obj.hexdigest()
        
        # Ensure the new UID is valid according to DICOM standards
        # Use a prefix that indicates this is an anonymized UID
        prefix = "2.25." # Registered prefix for locally generated UIDs
        
        # Convert hash to a number sequence and truncate to ensure valid length
        numeric_hash = int(hashed[:16], 16)
        
        return f"{prefix}{numeric_hash}"
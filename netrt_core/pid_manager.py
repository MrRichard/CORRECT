import os
import json
import logging
import threading
from datetime import datetime
from hashlib import sha256

logger = logging.getLogger(__name__)

class PIDManager:
    """Manages consistent patient ID anonymization with persistent mapping."""
    
    def __init__(self, config):
        """
        Initialize the PID Manager.
        
        Args:
            config: Anonymization configuration dictionary
        """
        self.config = config
        self.site_code = config.get("site_code", "SITE")
        self.mapping_file = config.get("pid_mapping_file", "/mnt/shared/pid_mapping.json")
        self.use_consistent_pid = config.get("use_consistent_pid", True)
        
        # Thread lock for file operations
        self._lock = threading.Lock()
        
        # Load or initialize mapping
        self.mapping = self._load_mapping()
        
        logger.info(f"PIDManager initialized. Site: {self.site_code}, Mapping file: {self.mapping_file}")
        logger.info(f"Current mapping contains {len(self.mapping)} patient(s)")
    
    def _load_mapping(self):
        """Load the PID mapping from file, or create new if doesn't exist."""
        if os.path.exists(self.mapping_file):
            try:
                with open(self.mapping_file, 'r') as f:
                    mapping = json.load(f)
                logger.info(f"Loaded existing PID mapping with {len(mapping)} entries")
                return mapping
            except Exception as e:
                logger.error(f"Failed to load PID mapping from {self.mapping_file}: {e}")
                logger.warning("Starting with empty mapping")
                return {}
        else:
            logger.info(f"No existing mapping file found at {self.mapping_file}. Creating new.")
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.mapping_file), exist_ok=True)
            return {}
    
    def _save_mapping(self):
        """Save the current mapping to file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.mapping_file), exist_ok=True)
            
            # Write to temporary file first for atomicity
            temp_file = f"{self.mapping_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(self.mapping, f, indent=2)
            
            # Atomic rename
            os.rename(temp_file, self.mapping_file)
            logger.debug(f"Saved PID mapping with {len(self.mapping)} entries")
        except Exception as e:
            logger.error(f"Failed to save PID mapping to {self.mapping_file}: {e}")
    
    def _generate_hash_key(self, original_patient_id, patient_name=""):
        """
        Generate a consistent hash key from patient identifiers.
        
        Args:
            original_patient_id: Original PatientID
            patient_name: Original PatientName (optional, for extra uniqueness)
            
        Returns:
            str: Hash key for lookup
        """
        # Combine patient ID and name for uniqueness
        combined = f"{original_patient_id}|{patient_name}".encode('utf-8')
        return sha256(combined).hexdigest()[:16]  # Use first 16 chars of hash
    
    def _get_next_pid_number(self):
        """Get the next available PID number."""
        if not self.mapping:
            return 1
        
        # Extract all PID numbers and find max
        max_pid = 0
        for anon_data in self.mapping.values():
            try:
                # Extract number from format: SITE01_0001_20240101
                parts = anon_data['anonymized_id'].split('_')
                if len(parts) >= 2:
                    pid_num = int(parts[1])
                    max_pid = max(max_pid, pid_num)
            except (ValueError, KeyError, IndexError):
                continue
        
        return max_pid + 1
    
    def get_anonymized_id(self, original_patient_id, patient_name="", study_date=None):
        """
        Get or create anonymized patient ID.
        
        Args:
            original_patient_id: Original PatientID from DICOM
            patient_name: Original PatientName from DICOM
            study_date: Study date (YYYYMMDD format), uses today if None
            
        Returns:
            str: Anonymized patient ID in format SITE_####_YYYYMMDD
        """
        if not self.use_consistent_pid:
            # Fall back to simple random ID
            from uuid import uuid4
            return f"{self.site_code}_{uuid4().hex[:8].upper()}"
        
        with self._lock:
            # Generate lookup key
            hash_key = self._generate_hash_key(original_patient_id, patient_name)
            
            # Check if we've seen this patient before
            if hash_key in self.mapping:
                logger.info(f"Found existing anonymized ID for patient: {self.mapping[hash_key]['anonymized_id']}")
                return self.mapping[hash_key]['anonymized_id']
            
            # Create new anonymized ID
            pid_number = self._get_next_pid_number()
            
            # Use study date if provided, otherwise use today
            if study_date:
                # Ensure it's in YYYYMMDD format
                if len(study_date) == 8 and study_date.isdigit():
                    date_str = study_date
                else:
                    date_str = datetime.now().strftime("%Y%m%d")
            else:
                date_str = datetime.now().strftime("%Y%m%d")
            
            # Format: SITE01_0001_20240101
            anonymized_id = f"{self.site_code}_{pid_number:04d}_{date_str}"
            
            # Store mapping
            self.mapping[hash_key] = {
                "anonymized_id": anonymized_id,
                "first_seen": datetime.now().isoformat(),
                "original_patient_id_hash": hash_key,  # Don't store actual ID for security
                "study_count": 1
            }
            
            # Save to file
            self._save_mapping()
            
            logger.info(f"Created new anonymized ID: {anonymized_id} (PID #{pid_number})")
            return anonymized_id
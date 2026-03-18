import yaml
import os
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "dicom_listener": {
        "host": "0.0.0.0",
        "port": 11112,
        "ae_title": "CORRECT",
        "config_negotiated_transfer_syntax": True # New: to control if negotiated TS is used for saving
    },
    "dicom_destination": {
        "ip": "127.0.0.1",
        "port": 104,
        "ae_title": "DEST_AET"
    },
    "directories": {
        "working": "~/CORRECT_working",
        "logs": "~/CORRECT_logs",
        "quarantine_subdir": "quarantine"
    },
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        "application_log_file": "application.log",
        "transaction_log_file": "transaction.log",
        "transaction_log_format": "%(asctime)s TXN [%(levelname)s]: %(message)s"
    },
    "anonymization": {
        "enabled": True,
        "full_anonymization_enabled": False, 
        "default_tags_to_remove": [ 
            "AccessionNumber", 
            "PatientID" 
        ],
        "default_tags_to_blank": [ "AccessionNumber", "PatientID" ], # Tags to blank if full_anonymization_enabled is false
        "full_anonymization_rules": { # Rules for full anonymization
            "tags_to_remove": [
                "PatientAddress", "PatientTelephoneNumbers", "PatientMotherBirthName",
                "OtherPatientIDs", "OtherPatientNames", "PatientBirthName", "PatientSize",
                "MilitaryRank", "BranchOfService", "EthnicGroup", "PatientComments",
                "DeviceSerialNumber", "PlateID", "InstitutionName", "InstitutionAddress",
                "ReferringPhysicianName", "ReferringPhysicianAddress", "ReferringPhysicianTelephoneNumbers",
                "PhysiciansOfRecord", "OperatorsName", "AdmittingDiagnosesDescription"
            ],
            "tags_to_empty": [
                "StudyID", "PerformingPhysicianName", "RequestingPhysician"
            ],
            "tags_to_modify_date": ["StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "PatientBirthDate"],
            "tags_to_modify_time": ["StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime"],
            "tags_to_regenerate_uid": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID", "MediaStorageSOPInstanceUID"],
            "patient_id_override": "ANONYMIZED_ID",
            "patient_name_override": "ANONYMIZED_NAME"
        }
    },
   "processing": {
        "ignore_contour_names_containing": ["skull"],
        "add_burn_in_disclaimer": True,
        "burn_in_text": "FOR RESEARCH USE ONLY - NOT FOR CLINICAL USE",
        "overlay_series_number": 98,
        "overlay_series_description": "RESEARCH ONLY: Unapproved Treatment Plan CT w Mask",
        "overlay_study_id": "RTPlanShare",
        "gsps_series_number": 100,
        "segmentation_series_number": 99,
        "segmentation_series_description_template": "RESEARCH USE ONLY: CONTOUR {}",
        "segmentation_algorithm_name": "Radiation Oncologist",
        "segmentation_algorithm_version": "v1.0",
        "segmentation_tracking_id": "FOR RESEARCH USE ONLY",
        "debug": {
            "generate_jpg_visualizations": False,
            "generate_secondary_capture_dicom": False,
        },
    },
    "watcher": {
        "debounce_interval_seconds": 60,
        "min_file_count_for_processing": 2
    },
    "feature_flags": {
        "enable_segmentation_export": False
    }
}

def load_config(config_path="config.yaml"):
    """Loads configuration from a YAML file, using defaults if the file doesn't exist or is incomplete."""
    # Create a deep copy of default_config to avoid modifying the global default
    config = yaml.safe_load(yaml.safe_dump(DEFAULT_CONFIG)) 

    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_config = yaml.safe_load(f)
            if user_config:
                # Deep merge user_config into config
                def _deep_merge_dicts(base, new_val):
                    for key, value in new_val.items():
                        if isinstance(value, dict) and isinstance(base.get(key), dict):
                            _deep_merge_dicts(base[key], value)
                        else:
                            base[key] = value
                    return base
                
                config = _deep_merge_dicts(config, user_config)
                logger.info(f"Successfully loaded and merged configuration from {config_path}")
            else:
                logger.warning(f"Configuration file {config_path} is empty. Using default configuration.")
                _save_default_config(config_path, config) # Save defaults if file was empty

        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML configuration file {config_path}: {e}. Using default configuration.")
        except Exception as e:
            logger.error(f"Error loading configuration file {config_path}: {e}. Using default configuration.")
    else:
        logger.warning(f"Configuration file {config_path} not found. Using default configuration and creating it.")
        _save_default_config(config_path, config)
    
    # Expand user paths for directories
    if "directories" in config:
        for key, path_val in config["directories"].items():
            if isinstance(path_val, str) and "subdir" not in key.lower(): # Don't expand subdir names
                 config["directories"][key] = os.path.expanduser(path_val)

    return config

def _save_default_config(config_path, config_data):
    """Saves the provided configuration data (usually defaults) to the specified path."""
    try:
        # Ensure the directory for the config file exists, especially if it's not in the current dir
        config_dir = os.path.dirname(config_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)
            logger.info(f"Created directory for configuration file: {config_dir}")

        with open(config_path, "w") as f:
            yaml.dump(config_data, f, sort_keys=False, default_flow_style=False, indent=4)
        logger.info(f"Saved default configuration to {config_path}")
    except Exception as e:
        logger.error(f"Could not save default configuration to {config_path}: {e}")
# NETRT Configuration Guide

Configuration is managed through a YAML file (default: `config.yaml`). If the file is missing or invalid, default values are used and a new configuration file is created.

## Configuration Structure

### DICOM Listener

```yaml
dicom_listener:
  host: "0.0.0.0"                    # IP address to bind to
  port: 11112                        # TCP port number
  ae_title: "NETRTCORE"              # Application Entity Title
  config_negotiated_transfer_syntax: true  # Use negotiated transfer syntax
```

### DICOM Destination

```yaml
dicom_destination:
  ip: "127.0.0.1"                    # Destination IP address
  port: 104                          # Destination port
  ae_title: "DEST_AET"               # Destination AE Title
```

### Directories

```yaml
directories:
  working: "~/CORRECT_working"          # Study processing directory
  logs: "~/CORRECT_logs"                # Log file directory
  quarantine_subdir: "quarantine"   # Quarantine subdirectory name
```

Paths starting with `~/` are expanded to the user's home directory.

### Features

```yaml
features:
  send_original_series: true         # Forward the original CT series unchanged
  create_augmented_series: true      # Create the overlay-plane CT series
  create_gsps: true                  # Create GSPS for the augmented series
  create_segmentation_export: true   # Create DICOM SEG objects
  create_secondary_capture: true     # Create derived RGB SC images
  generate_jpg_visualizations: true  # Create JPG debug renders
```

Use `features` as the on/off checklist. Keep detailed series numbers, labels, and algorithm metadata under `processing`.

### Processing Options

```yaml
processing:
  # Contour filtering
  ignore_contour_names_containing: ["skull"]
  
  # Overlay series settings
  overlay_series_number: 98
  overlay_series_description: "Processed DicomRT with Overlay"
  overlay_study_id: "RTPlanShare"
  
  # Burn-in disclaimer
  add_burn_in_disclaimer: true
  burn_in_text: "FOR RESEARCH USE ONLY - NOT FOR CLINICAL USE"
  
  # Segmentation series (if enabled)
  segmentation_series_number: 99
  segmentation_series_description_template: "RESEARCH USE ONLY: CONTOUR {}"
  segmentation_algorithm_name: "Radiation Oncologist"
  segmentation_algorithm_version: "v1.0"
  segmentation_tracking_id: "FOR RESEARCH USE ONLY"

  # GSPS display recommendation
  gsps_series_number: 100
  gsps_overlay_color_cielab: [34895, 53534, 50196]

  # Secondary Capture series
  sc_series_number: 101
  sc_series_description: "SC: Contour Overlay Visualization"
```

### Anonymization

```yaml
anonymization:
  enabled: true
  site_code: "WFU"
  pid_mapping_file: "/mnt/shared/pid_mapping.json"
  study_description: "CORRECT Study Treatment Plan"
  rules:
    remove_tags:
      - "AccessionNumber"
      - "PatientID"
      - "PatientBirthDate"
    blank_tags: []
```

### File System Watcher

```yaml
watcher:
  debounce_interval_seconds: 60
  min_file_count_for_processing: 2
```

### Logging

```yaml
logging:
  level: "INFO"                      # Logging level
  format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
  application_log_file: "application.log"
  transaction_log_file: "transaction.log"
  transaction_log_format: "%(asctime)s TXN [%(levelname)s]: %(message)s"
```

## Command Line Arguments

- `--config <path>`: Specify configuration file path (default: config.yaml)
- `--debug`: Enable debug visualization mode

## Default Behavior

If configuration sections are missing, the following defaults apply:

- **DICOM Listener**: Listens on all interfaces (0.0.0.0) port 11112
- **Working Directory**: `~/CORRECT_working`
- **Anonymization**: Enabled, removes AccessionNumber and PatientID
- **Features**: All output features default to enabled
- **Processing**: Ignores contours containing "skull", adds burn-in disclaimer
- **Logging**: INFO level to both console and files

## Example Configuration

```yaml
dicom_listener:
  host: "152.11.105.71"
  port: 11116
  ae_title: "CORRECT_DEV"

dicom_destination:
  ip: "152.11.105.71"
  port: 4242
  ae_title: "RADIORIIPL"

processing:
  ignore_contour_names_containing: ["skull"]
  overlay_series_number: 999
  overlay_series_description: "RESEARCH ONLY: Treatment Plan CT w Mask"
  add_burn_in_disclaimer: true

features:
  send_original_series: true
  create_augmented_series: true
  create_gsps: true
  create_segmentation_export: true
  create_secondary_capture: true
  generate_jpg_visualizations: true
```

# CORRECT Technical & Deployment Guide

This guide provides a concise overview of the CORRECT architecture, processing details, and deployment procedures.

## Architecture Overview

CORRECT is an event-driven DICOM service built around the `netrt_core` package.

### Core Components
- **DICOM Listener (`dicom_listener.py`)**: pynetdicom C-STORE SCP server.
- **FileSystemManager (`file_system_manager.py`)**: Manages study directories and monitors for completion using `watchdog`.
- **StudyProcessor (`study_processor.py`)**: Orchestrates the 6-step processing pipeline.
- **ContourProcessor (`contour_processor.py`)**: Extracts ROI data using `rt-utils` and generates overlay masks.
- **DICOM Sender (`dicom_sender.py`)**: C-STORE SCU client for result transmission.

## Processing Workflow

1.  **Reception**: Files stored in `UID_<StudyUID>/{DCM,Structure}`.
2.  **Detection**: Debounce monitor (default 5s) triggers processing once file activity ceases.
3.  **Validation**: Verifies required CT/MR and RTSTRUCT components.
4.  **Anonymization**: Optional tag stripping and site-code ID replacement.
5.  **Processing**: Contours merged into binary masks and added as Group 0x6000 overlays.
6.  **Transmission**: Processed series sent to destination; temporary files cleared on success.

## Deployment

### Docker (Recommended)
1.  **Configure**: Set DICOM parameters in `config.yaml`.
2.  **Directories**: Create host paths for `working` and `logs`.
3.  **Run**:
    ```bash
    docker compose up --build -d
    docker compose logs -f
    ```

### Systemd (Bare Metal)
1.  **Dependencies**: `pip install -r requirements.txt`.
2.  **Service**: Use `netrt.service.example` as a template for `/etc/systemd/system/netrt.service`.
3.  **Manage**:
    ```bash
    sudo systemctl start netrt.service
    sudo journalctl -u netrt.service -f
    ```

## Network & Security
- **Listener**: Port 11112 (default). Bind to specific interfaces for security.
- **Firewall**: Restrict access to port 11112 to known imaging sources.
- **Verification**: Use `echoscu` from `dcmtk` to test connectivity.
- **Anonymization**: Review `apply_site_code` and `apply_anonymization_rules` in `config.yaml`.

## Maintenance
- **Logs**: Monitor `application.log` (system) and `transaction.log` (audit).
- **Quarantine**: Studies that fail processing are moved to the `quarantine` subdirectory for manual review.
- **Storage**: Monitor disk usage in the `working` directory.

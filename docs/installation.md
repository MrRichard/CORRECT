# CORRECT — Installation Guide
**RHEL / Rocky Linux (Bare Metal or VM)**

---

## Prerequisites

- RHEL 8/9 or Rocky Linux 8/9
- Python 3.10 or later
- `git` (to clone the repository)
- Network access to the planning workstation and destination PACS/VNA
- Sudo or root access during installation

---

## 1. Install System Dependencies

```bash
sudo dnf update -y
sudo dnf install -y python3 python3-pip python3-venv git
```

Verify the Python version:

```bash
python3 --version
```

---

## 2. Create a Dedicated Service Account

Running CORRECT under a dedicated user is recommended.

```bash
sudo useradd -r -m -d /opt/correct -s /sbin/nologin correct
```

---

## 3. Deploy the Application

Copy or clone the application into the service account's home directory:

```bash
sudo -u correct git clone <repository-url> /opt/correct/app
```

Or if deploying from a local archive:

```bash
sudo mkdir -p /opt/correct/app
sudo tar -xzf correct.tar.gz -C /opt/correct/app
sudo chown -R correct:correct /opt/correct/app
```

---

## 4. Create a Python Virtual Environment

```bash
sudo -u correct python3 -m venv /opt/correct/venv
```

---

## 5. Install Python Dependencies

```bash
sudo -u correct /opt/correct/venv/bin/pip install --upgrade pip
sudo -u correct /opt/correct/venv/bin/pip install -r /opt/correct/app/requirements.txt
```

---

## 6. Create Working Directories

```bash
sudo mkdir -p /opt/correct/working
sudo mkdir -p /opt/correct/logs
sudo chown -R correct:correct /opt/correct/working /opt/correct/logs
```

---

## 7. Configure the Application

Create the configuration file at `/opt/correct/app/config.yaml`. A fully documented example is provided below.

```bash
sudo -u correct nano /opt/correct/app/config.yaml
```

### Example `config.yaml`

```yaml
# ─────────────────────────────────────────────────────────────────────────────
# DICOM Listener
# This is the CORRECT application itself. The planning workstation will send
# directly to this host on the port and AE title configured here.
# ─────────────────────────────────────────────────────────────────────────────
dicom_listener:
  host: 0.0.0.0          # Listen on all interfaces
  port: 11112             # Standard DICOM port is 104; 11112 is conventional for
                          # application-layer services running as a non-root user.
                          # See firewall note in Section 8 if using port 104.
  ae_title: CORRECT       # AE title this application will answer to

# ─────────────────────────────────────────────────────────────────────────────
# DICOM Destination
# The PACS or VNA that CORRECT will forward processed studies to.
# ─────────────────────────────────────────────────────────────────────────────
dicom_destination:
  ip: 192.168.1.50        # IP address of the destination PACS or VNA
  port: 104               # Standard DICOM port
  ae_title: PACS_AET      # AE title of the destination system

# ─────────────────────────────────────────────────────────────────────────────
# Directories
# ─────────────────────────────────────────────────────────────────────────────
directories:
  working: /opt/correct/working     # Temporary storage for in-progress studies
  logs: /opt/correct/logs           # Application and transaction log output
  quarantine_subdir: quarantine     # Subdirectory name for failed studies

# ─────────────────────────────────────────────────────────────────────────────
# File Watcher / Debounce
# Controls how long to wait after the last received file before triggering
# processing. Increase this value if large studies are being split across
# slow network pushes.
# ─────────────────────────────────────────────────────────────────────────────
watcher:
  debounce_interval_seconds: 60
  min_file_count_for_processing: 10  # Minimum files required before processing

# ─────────────────────────────────────────────────────────────────────────────
# Anonymization
# When enabled, PatientID and PatientName are replaced with a generated code
# in the format:  <site_code>_<####>_<YYYYMMDD>
# e.g.  INST_0001_20250115
# A mapping file is maintained locally to ensure consistent IDs across
# studies from the same patient. The original ID is never stored — only
# a one-way hash.
# ─────────────────────────────────────────────────────────────────────────────
anonymization:
  enabled: true
  site_code: INST            # Short identifier for your institution
  pid_mapping_file: /opt/correct/working/pid_mapping.json
  study_description: "CORRECT Study Treatment Plan"

  rules:
    remove_tags:
      - AccessionNumber
      - ReferringPhysicianName
      - PatientBirthDate
      - PatientAge
      - PatientWeight
      - PatientAddress
      - InstitutionName
      - InstitutionAddress
      - OperatorsName
      - PerformingPhysicianName
      - PhysiciansOfRecord
      - RequestingPhysician
      - StudyID
    blank_tags: []

# ─────────────────────────────────────────────────────────────────────────────
# Processing
# ─────────────────────────────────────────────────────────────────────────────
processing:
  # Contour names containing any of these substrings (case-insensitive) will
  # be excluded from overlay generation.
  ignore_contour_names_containing:
    - skull

  # Burn-in disclaimer added to pixel data of all output overlay images.
  add_burn_in_disclaimer: true
  burn_in_text: "FOR RESEARCH USE ONLY - NOT FOR CLINICAL USE"

  overlay_series_number: 999
  overlay_series_description: "RESEARCH ONLY: Unapproved Treatment Plan CT w Mask"
  overlay_study_id: RTPlanShare

  segmentation_series_number: 99
  segmentation_series_description_template: "RESEARCH USE ONLY: CONTOUR {}"
  segmentation_algorithm_name: "Radiation Oncologist"
  segmentation_algorithm_version: "v1.0"
  segmentation_tracking_id: "FOR RESEARCH USE ONLY"

# ─────────────────────────────────────────────────────────────────────────────
# Feature Flags
# ─────────────────────────────────────────────────────────────────────────────
feature_flags:
  enable_segmentation_export: true   # Set to true to export DICOM SEG objects

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging:
  level: INFO
  application_log_file: application.log
  transaction_log_file: transaction.log
```

---

## 8. Configure the Firewall

Allow inbound DICOM traffic on the listener port:

```bash
sudo firewall-cmd --permanent --add-port=11112/tcp
sudo firewall-cmd --reload
```

> **Note:** Port 104 is the IANA-assigned DICOM port and may be expected by some systems. Binding to ports below 1024 requires elevated privileges. To allow CORRECT to bind to port 104 without running as root, grant the capability to the Python interpreter:
>
> ```bash
> sudo setcap cap_net_bind_service=+ep /opt/correct/venv/bin/python3
> ```

---

## 9. Verify the Installation

Run CORRECT manually to confirm it starts cleanly:

```bash
sudo -u correct /opt/correct/venv/bin/python3 /opt/correct/app/main.py \
    --config /opt/correct/app/config.yaml
```

The application will log startup messages and begin listening. Send a DICOM C-ECHO from the planning workstation to confirm network connectivity.

Press `Ctrl+C` to stop.

---

## 10. (Optional) Install as a systemd Service

### Create the Unit File

```bash
sudo nano /etc/systemd/system/correct.service
```

Paste the following:

```ini
[Unit]
Description=CORRECT DICOM Processing Service
After=network.target

[Service]
Type=simple
User=correct
Group=correct
WorkingDirectory=/opt/correct/app
ExecStart=/opt/correct/venv/bin/python3 /opt/correct/app/main.py \
    --config /opt/correct/app/config.yaml
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable correct
sudo systemctl start correct
```

### Check Status and Logs

```bash
sudo systemctl status correct
sudo journalctl -u correct -f
```

### Stop / Restart

```bash
sudo systemctl stop correct
sudo systemctl restart correct
```

---

## Directory Reference

| Path | Purpose |
|---|---|
| `/opt/correct/app/` | Application source code |
| `/opt/correct/app/config.yaml` | Configuration file |
| `/opt/correct/venv/` | Python virtual environment |
| `/opt/correct/working/` | Temporary study storage during processing |
| `/opt/correct/working/quarantine/` | Studies that failed processing |
| `/opt/correct/working/pid_mapping.json` | Anonymized patient ID mapping (local only) |
| `/opt/correct/logs/` | Application and transaction logs |

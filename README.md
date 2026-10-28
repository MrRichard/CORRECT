# CORRECT — DICOM RT Structure Processor

CORRECT is a DICOM service that automatically processes RT Structure Sets to create
contour overlay series. It listens for DICOM studies on a network port, extracts contour
data from RTSTRUCT files, merges them into binary masks, and creates new DICOM series with
graphical overlays, GSPS objects, and optional SEG exports.

## Key Features

- **DICOM Network Listener**: Receives DICOM studies via C-STORE operations
- **Automated RT Processing**: Extracts and merges contour data from RTSTRUCT files
- **Overlay / GSPS / SEG Generation**: Contour masks as overlay planes, Softcopy Presentation States, and DICOM SEG objects
- **Configurable Anonymization**: Independent site-code ID replacement and tag removal/blanking rules
- **Debug Visualization**: Optional JPG and DICOM debug output for quality assurance
- **Transaction Logging**: Detailed audit trails for all processing operations

---

## Installation

CORRECT can be deployed three ways. **For a server, the installer script is recommended.**

| Method | Best for | Section |
|---|---|---|
| **Installer script** (`install/install.sh`) | Debian/Ubuntu or RHEL/Rocky/Fedora servers | [Installer Script](#installer-script-recommended) |
| **Docker Compose** | Containerized / isolated deployments | [Docker Compose](#docker-compose) |
| **Direct local install** (Python 3.11) | Development or manual control | [Direct Local Install](#direct-local-install-python-311) |

### Installer Script (recommended)

A single script handles Debian/Ubuntu and RHEL/Rocky/Fedora. It installs Python 3.11,
creates a dedicated `correct` service account, clones the repo, builds a virtualenv, writes
the systemd unit, opens the firewall, and enables the service — all matching the OVA appliance
layout (`/opt/correct/app/{src,venv,config.yaml}`).

```bash
git clone https://github.com/MrRichard/CORRECT
cd CORRECT
sudo ./install/install.sh                 # defaults: branch master, port 11112
```

Options:

```bash
sudo ./install/install.sh --branch main           # deploy a specific branch/tag
sudo ./install/install.sh --config /path/cfg.yaml # install a pre-made site config
sudo ./install/install.sh --port 11112            # listener + firewall port
sudo ./install/install.sh --dry-run               # print every step, change nothing
sudo ./install/install.sh --help
```

After install, edit the config and restart:

```bash
sudo nano /opt/correct/app/config.yaml            # set dicom_destination, site_code, etc.
sudo systemctl restart correct
sudo systemctl status correct
```

> **Fedora 43 note:** Fedora 43 ships Python 3.14 as the default `python3`, but the pinned
> dependency stack (numpy 2.2.2, scipy 1.15.1, opencv-python 4.11.0.86) has no 3.14 wheels.
> The installer detects Fedora and uses the `python3.13` package automatically. See
> [`docs/installation-fedora43.md`](docs/installation-fedora43.md) for details.

### Docker Compose

#### Prerequisites

- Docker and Docker Compose (or rootful `podman` + `podman-compose` on Fedora/RHEL)

#### Setup

1. **Clone and configure**

   ```bash
   git clone https://github.com/MrRichard/CORRECT
   cd CORRECT
   ```

2. **Edit configuration** — modify `config.yaml`:

   ```yaml
   dicom_listener:
     host: "0.0.0.0"
     port: 11112
     ae_title: "CORRECT"

   dicom_destination:
     ip: "192.168.1.100"
     port: 104
     ae_title: "DESTINATION_AET"
   ```

3. **Create data directories**

   ```bash
   mkdir -p ~/NETRT/data/{working,logs,output}
   cp config.yaml ~/NETRT/config.yaml
   ```

4. **Deploy**

   ```bash
   docker compose up --build -d
   ```

5. **Verify**

   ```bash
   docker compose logs -f
   ```

#### Management

- **Logs**: `docker compose logs -f`
- **Stop**: `docker compose down`
- **Restart**: `docker compose restart`

A local PACS stack (Orthanc) is also available via `docker-compose-pacs.yml` with
`config.pacs-example.yaml` — see [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

### Direct Local Install (Python 3.11)

For development or manual deployments on any Linux host.

#### Prerequisites

- **Python 3.11** installed (the application and pinned dependencies target 3.11)
- `git`
- Network access to source and destination DICOM systems

#### Setup

1. **Clone and create a virtualenv**

   ```bash
   git clone https://github.com/MrRichard/CORRECT
   cd CORRECT
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install -e .
   ```

   > `pip install -e .` installs the `correct` console command (defined in `pyproject.toml`)
   > and activates the runtime shim in `netrt_core/rt_utils_compat.py` that patches the known
   > `rt-utils==1.2.7` `NameError` bug. Always launch via `correct` or `main.py` so the shim
   > is armed — never import `rt_utils` directly.

2. **Edit configuration** — modify `config.yaml` (same keys as the Docker section above).

3. **Create data directories**

   ```bash
   mkdir -p ~/CORRECT_working ~/CORRECT_logs
   ```

4. **Run**

   ```bash
   python main.py --config config.yaml
   # or, if installed as a script:
   correct --config config.yaml
   ```

---

## Updating an Installation

### Generic updater (bare-metal or VM)

```bash
sudo ./install/update.sh                  # pull master, refresh deps, restart
sudo ./install/update.sh --branch dev     # track a different branch
sudo ./install/update.sh --no-restart     # update code only
```

The runtime config at `/opt/correct/app/config.yaml` lives outside the source tree, so it is
preserved automatically across updates.

### OVA / VM appliance updater

For deployed CORRECT virtual appliances (Debian Trixie, built via `correct-ova-build`):

```bash
sudo ./install/update-vm.sh               # self-contained; safe to copy onto a VM
sudo ./install/update-vm.sh --branch master
```

`update-vm.sh` backs up the site config, pulls the latest code, reinstalls dependencies
(including the `pip install -e .` transition for older OVA images that predate `pyproject.toml`),
verifies the ufw rule, restarts the service, and runs the post-update verification checks.

For the manual VM update procedure (preserving local config on a raw `git pull`), see
[`docs/VM_UPDATE_GUIDE.md`](docs/VM_UPDATE_GUIDE.md).

---

## Uninstalling

```bash
sudo ./install/uninstall.sh               # remove service + unit + firewall rule (keeps data/user)
sudo ./install/uninstall.sh --purge       # also remove user, source, venv, config, and all data
sudo ./install/uninstall.sh --purge --keep-config   # purge but save config.yaml to /tmp
```

`--purge` returns the system to a pre-install state.

---

## Configuration

All settings are managed through `config.yaml`. Key parameters:

- **`dicom_listener`** — host, port, AE title for the inbound C-STORE listener
- **`dicom_destination`** — IP, port, AE title of the PACS/VNA to forward processed studies to
- **`directories`** — working, logs, and quarantine paths (`~` is expanded)
- **`anonymization`** — site-code ID replacement + tag removal/blanking rules
- **`processing.target_contour_names_containing`** — optional (defaults to `["GTV"]`); a study
  with no matching contour fails and is quarantined
- **`features`** — toggle overlay / GSPS / SEG / secondary-capture / JPG exports

See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the complete reference.

## Processing Workflow

1. **Reception**: DICOM files received via C-STORE and organized by StudyInstanceUID
2. **Detection**: File-system monitoring triggers processing after transfer completion
3. **Processing**: RT Structure contours extracted, filtered, and merged into overlay masks
4. **Output**: New DICOM series created with contour overlays, GSPS, and optional SEG
5. **Transmission**: Processed series sent to the configured destination
6. **Cleanup**: Temporary files removed after successful transmission

## Documentation

- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — complete configuration reference
- [`docs/TECHNICAL_GUIDE.md`](docs/TECHNICAL_GUIDE.md) — technical architecture and deployment
- [`docs/installation.md`](docs/installation.md) — RHEL/Rocky bare-metal installation guide
- [`docs/installation-fedora43.md`](docs/installation-fedora43.md) — Fedora 43 installation guide
- [`docs/VM_UPDATE_GUIDE.md`](docs/VM_UPDATE_GUIDE.md) — VM update procedure (preserve local config)
- [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md) — security evaluation and risk disclosure
- [`docs/LICENSE`](docs/LICENSE) — MIT License

## Requirements

- **Python 3.11+** for local runs (the Docker image pins `python:3.11-slim`)
- DICOM network connectivity
- Sufficient disk space for temporary study storage

## Production Considerations

- Configure disk-space monitoring for working directories
- Set up log rotation for application and transaction logs
- Implement network security controls for DICOM communications
- Review anonymization settings (`apply_site_code`, `apply_anonymization_rules`) to meet your privacy requirements
- Monitor the quarantine directory for failed studies (cleared automatically on startup)

## Support

For configuration issues, check the application logs in the configured logs directory.
Failed studies are automatically moved to the quarantine subdirectory for manual review.
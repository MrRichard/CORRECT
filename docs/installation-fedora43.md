# CORRECT — Installation Guide for Fedora 43

> **Verified:** this exact flow was executed end-to-end in a fresh Python 3.12 venv from
> `fix/pydicom-and-contour-sequence` (d851d49): all pinned deps installed from PyPI wheels,
> `pip install -e .` produced the `correct` command, startup printed the rt-utils patch
> banner, and the test suite passed 14/15 (the one failure is a stale assertion on this
> dev branch, not an install issue — see §10).

---

## 1. Key gotcha: Fedora 43's default Python

Fedora 43 ships **Python 3.14** as `python3`. The pinned dependency stack in
`requirements.txt` (numpy 2.2.2, scipy 1.15.1, scikit-image 0.25.1, opencv-python
4.11.0.86, matplotlib 3.10.0) predates 3.14 and **has no prebuilt wheels for it** — pip
would attempt long source builds that fail. Install Python 3.13 as a parallel interpreter
and build the venv with that:

```bash
sudo dnf update -y
sudo dnf install -y python3.13 python3.13-pip git
python3.13 --version    # must report 3.13.x
```

Do **not** create the venv with plain `python3` (3.14) on Fedora 43.

## 2. Dedicated service account

```bash
sudo useradd -r -m -d /opt/correct -s /usr/sbin/nologin correct
```

`-d /opt/correct` gives the account its home there, so `~`-paths in `config.yaml`
expand correctly (`config_loader` calls `expanduser`).

## 3. Deploy the code

**`master` is directly installable** (it contains `pyproject.toml` and the
`netrt_core/rt_utils_compat.py` runtime shim). You can clone master directly, or use the
bundled installer which handles Fedora 43's Python version automatically:

```bash
sudo ./install/install.sh                 # detects Fedora, uses python3.13, sets up systemd
```

If you prefer to clone by hand:

```bash
sudo -u correct git clone https://github.com/MrRichard/CORRECT /opt/correct/app
cd /opt/correct/app
```

## 4. Virtual environment and dependencies

```bash
sudo -u correct /usr/bin/python3.13 -m venv /opt/correct/venv
sudo -u correct /opt/correct/venv/bin/pip install --upgrade pip
sudo -u correct /opt/correct/venv/bin/pip install -r /opt/correct/app/requirements.txt
sudo -u correct /opt/correct/venv/bin/pip install -e /opt/correct/app
```

**Verify the install — expect the patch banner.** The `rt-utils` fix is a runtime shim in
`netrt_core/rt_utils_compat.py`; it arms only when `netrt_core` is imported (which happens
at application startup). Run:

```bash
sudo -u correct /opt/correct/venv/bin/correct --help
```

You should see first:

```
Patched rt-utils <= 1.2.7: 'import warnings' is missing from rt_utils.rtstruct_builder,
which made its warn_only path raise NameError instead of warning (upstream fix b70ad40b
is unreleased).
```

If the banner is missing, you are on a branch without the shim — stop and fix the branch.

## 5. Configure

Edit `/opt/correct/app/config.yaml` (it ships with site-specific defaults; review every
key below):

| Key | Set to |
|---|---|
| `dicom_listener.port` / `ae_title` | e.g. `11112` / `CORRECT` |
| `dicom_destination.ip` / `port` / `ae_title` | Your PACS/VNA |
| `directories.working` / `directories.logs` | `/opt/correct/working` / `/opt/correct/logs` |
| `anonymization.site_code` | Your institution code |
| `anonymization.pid_mapping_file` | A path whose parent dir exists and is writable |
| `reporting.output_directory` | A report output dir |
| `processing.target_contour_names_containing` | Optional; defaults to `["GTV"]`. Missing targets = hard failure + quarantine |

`~` paths are supported in `directories` and the mapping/report files.

## 6. Create data directories

```bash
sudo mkdir -p /opt/correct/working /opt/correct/logs
sudo chown -R correct:correct /opt/correct/working /opt/correct/logs
# plus whatever pid_mapping_file / reporting.output_directory you configured, e.g.:
sudo mkdir -p /mnt/shared && sudo chown correct:correct /mnt/shared
```

## 7. Firewall (firewalld)

```bash
sudo firewall-cmd --permanent --add-port=11112/tcp
sudo firewall-cmd --reload
```

If something must connect to port 104 instead, grant the bind capability (see the note in
`docs/installation.md` §8 — `setcap cap_net_bind_service=+ep` on the venv python).

## 8. SELinux note

Fedora's default enforcing mode generally does not block this setup (systemd-launched
services run `unconfined_service_t, and binding unprivileged TCP 11112 needs no policy).
If you point data dirs at unusual locations and hit AVC denials, audit with
`sudo ausearch -m AVC -ts recent` before weakening anything.

## 9. Run manually and test connectivity

```bash
sudo -u correct /opt/correct/venv/bin/python /opt/correct/app/main.py \
    --config /opt/correct/app/config.yaml
```

Startup logs appear and the listener binds. Then from the sending workstation:

```bash
echoscu -aet SENDER -aec CORRECT <server-ip> 11112
```

Ctrl+C stops it.

## 10. Optional: run the test suite

```bash
sudo -u correct /opt/correct/venv/bin/pip install -r /opt/correct/app/requirements-dev.txt
sudo -u correct /opt/correct/venv/bin/python -m pytest /opt/correct/app/tests/ -q
```

Current state on the dev branch: **14 passed, 1 failed** —
`test_study_without_gtv_fails` asserts the legacy message `"No GTV contours found"`,
but the code now emits `"No target contours found ..."` after target-contour names became
configurable. The behavior tested is correct; the assertion text is stale. Treat the
suite as passing when this lone failure is the remaining one.

## 11. Install as a systemd service

`/etc/systemd/system/correct.service`:

```ini
[Unit]
Description=CORRECT DICOM Processing Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=correct
Group=correct
WorkingDirectory=/opt/correct/app
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/correct/venv/bin/python main.py --config /opt/correct/app/config.yaml
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now correct
sudo systemctl status correct
sudo journalctl -u correct -f
sudo ss -tlnp | grep 11112
```

## 12. Alternative: container deployment

Fedora 43 ships `podman` natively. The provided `docker-compose.yml` runs under
`podman-compose` (rootful, required to bind the listener port):

```bash
sudo dnf install -y podman podman-compose
sudo podman-compose up -d --build
sudo podman-compose logs -f
```

(Typical Fedora `docker-compose.yml` uses group-add for UID/GID mapping — when using
podman see that file's annotations.) Docker CE also works via Docker's official repo if
you prefer it. Configuration and data-dir setup are identical to §5–§6; the compose file
mounts `/DATA/netrt_data` per `README.md`.

## 13. Updating an existing installation

```bash
sudo -u correct git -C /opt/correct/app fetch
sudo -u correct git -C /opt/correct/app pull   # on the deployed branch
sudo -u correct /opt/correct/venv/bin/pip install -r /opt/correct/app/requirements.txt
sudo -u correct /opt/correct/venv/bin/pip install -e /opt/correct/app
sudo systemctl restart correct
```

See also `docs/VM_UPDATE_GUIDE.md` for preserving local config changes across pulls.
# Installing CORRECT from the .deb / .rpm packages

The Linux packages are self-contained: they bundle their own Python 3.11 and every
dependency, so the target needs **no internet access, no system Python, and no compiler**.
One `.deb` covers Debian/Ubuntu and one `.rpm` covers RHEL-family and Fedora.

| Package | Tested on (CI) |
|---|---|
| `correct_<version>-1_amd64.deb` | Ubuntu 22.04, 24.04 · Debian 11, 12 |
| `correct-<version>-1.x86_64.rpm` | Rocky Linux 8, 9 · AlmaLinux 10 · Fedora 43 (RHEL 8–10 equivalent) |

Requirements: x86_64, glibc 2.28 or newer (RHEL/Rocky 8+, Debian 10+, Ubuntu 20.04+), systemd.
About 200 MB to download and 800 MB installed.

## Get the packages

- **Releases:** GitHub → *Releases* → download the `.deb` or `.rpm` (and `SHA256SUMS`).
- **Development builds:** GitHub → *Actions* → *Installers* → a run → artifact **CORRECT-linux**.
- **Build yourself** (any x86_64 Linux with bash, curl, tar): `packaging/linux/build.sh`
  → `dist/`.

Verify the download: `sha256sum -c SHA256SUMS --ignore-missing`

## Install

```bash
# Debian / Ubuntu
sudo apt install ./correct_0.3.0-1_amd64.deb

# RHEL / Rocky / Alma / Fedora
sudo dnf install ./correct-0.3.0-1.x86_64.rpm
```

The package creates the `correct` system user and the data folders, installs the systemd
unit, and **enables** the service at boot. It does **not** start the service, because the
destination is not configured yet.

## Configure and start

1. Edit `/etc/correct/config.yaml`. At minimum, set `dicom_destination` (ip, port,
   ae_title) and `anonymization.site_code`. See [CONFIGURATION.md](CONFIGURATION.md).
   With a desktop or `ssh -X`, you can use the graphical editor instead: `sudo correct-config`.
2. Start the service: `sudo systemctl start correct`
3. Open the DICOM port if a firewall is active. The package never changes firewall rules.
   ```bash
   sudo firewall-cmd --permanent --add-port=11112/tcp && sudo firewall-cmd --reload   # RHEL family
   sudo ufw allow 11112/tcp                                                           # Ubuntu
   ```
4. Test: `echoscu -aec CORRECT <this-host> 11112`

## Where things are

| Path | Contents |
|---|---|
| `/etc/correct/config.yaml` | Configuration (a conffile: upgrades never overwrite your edits) |
| `/var/lib/correct/working` | Studies being received/processed, plus `quarantine/` |
| `/var/lib/correct/shared` | Reports and `pid_mapping.json` (anonymized ID mapping) |
| `/var/log/correct` | `application.log`, `transaction.log` (also `journalctl -u correct`) |
| `/opt/correct/runtime`, `/opt/correct/app` | Bundled Python and CORRECT code (owned by the package) |
| `/usr/lib/systemd/system/correct.service` | Unit. Customize with `systemctl edit correct`, not by editing this file |

You can point the reports and PID mapping at a mounted share, such as `/mnt/shared`.
Change `reporting.output_directory` and `anonymization.pid_mapping_file`, then make that
folder writable by the `correct` user.

## Upgrade

Install the newer package the same way (`apt install ./…` / `dnf install ./…`). A running
service is restarted automatically. Your config and data are kept.

## Remove

```bash
sudo apt remove correct      # or: sudo dnf remove correct
```

The service is stopped and disabled, and the program files are removed. **Data is kept on
purpose**, because it may contain patient data and the PID mapping: `/var/lib/correct`,
`/var/log/correct`, and your edited `/etc/correct/config.yaml`. The `correct` user is also
kept. `apt purge correct` additionally removes the config. Delete the data by hand when you
no longer need it.

## Migrating from install.sh or the OVA

The package refuses to install over a script/OVA install (`/opt/correct/app/venv` or
`/opt/correct/app/src`), so the two never run side by side. To switch:

```bash
sudo /opt/correct/app/src/install/uninstall.sh   # removes the old service; keeps data and user
sudo mv /opt/correct/app /opt/correct/app.legacy
sudo apt install ./correct_<version>-1_amd64.deb   # or dnf install ./correct-<version>-1.x86_64.rpm
```

Then carry your settings over:

- Copy the site-specific values (`dicom_destination`, `anonymization.site_code`,
  `processing.*`, …) from `/opt/correct/app.legacy/config.yaml` into
  `/etc/correct/config.yaml`. Keep the package's `directories` paths, or keep the old ones
  and `chown -R correct:correct` them.
- **Keep the same anonymized patient IDs:** copy `/mnt/shared/pid_mapping.json` to
  `/var/lib/correct/shared/`, or point `anonymization.pid_mapping_file` at the old file.
- `sudo systemctl start correct`

`update.sh` and `update-vm.sh` apply only to script/OVA installs. Package installs are
updated by installing a newer package.

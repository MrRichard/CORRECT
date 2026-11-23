# Security Evaluation and Risk Disclosure — CORRECT

This document describes an informal, best-effort review of the security posture of the CORRECT application. It is written for medical physicists and informatics staff deploying this tool on a hospital or research network — not for security auditors. Read it before you deploy.

---

## What Was Evaluated

The following components were reviewed by reading the source code. This is not a penetration test, a formal threat model, or a vulnerability scan.

| Component | What it does |
|---|---|
| `docker-compose.yml` / `Dockerfile` | Container build configuration and port exposure — port 11112 is mapped from the container to the host |
| `docker-entrypoint.sh` | Startup sequence; the container starts as root to fix volume permissions, then drops to a non-root `appuser` account via `gosu` |
| `netrt_core/dicom_listener.py` | The DICOM C-STORE server that receives incoming imaging studies from your planning system or scanner |
| `netrt_core/dicom_sender.py` | Sends processed studies to the destination PACS configured in `config.yaml` |
| `DicomAnonymizer.py` | Removes or replaces patient-identifying DICOM tags before transmission |
| `netrt_core/pid_manager.py` | Maintains a permanent file (`pid_mapping.json`) that links anonymized IDs back to original patient identifiers |
| `netrt_core/config_loader.py` | Reads and applies all runtime configuration |
| `netrt_core/file_system_manager.py` | Writes incoming DICOM files to disk and monitors for study completion before triggering processing |
| `config.yaml` | Deployment configuration, including the destination PACS IP address, port, and AE title |
| `correct_admin/` | Windows service, service-control CLI, configuration UI, and first-run setup (data-folder permissions, firewall rule) |
| `packaging/windows/` | Inno Setup installer and its build script (bundled Python runtime, download verification) |
| `packaging/linux/` | .deb/.rpm build, maintainer scripts, launcher scripts, and the systemd unit |

**What was NOT evaluated:** network infrastructure, operating system hardening, Docker host security, PACS configuration, or any component outside the list above.

---

## Understanding the Risks

### The Central Risk: Data Leaving Your Network

CORRECT receives DICOM studies and, after processing, transmits them to a destination system. That destination is configured in a single location in `config.yaml` under `destinations` (host, port, ae_title).

The application will transmit to whatever address is in that field. There is no confirmation prompt, no secondary validation, and no mechanism that compares the destination against an approved list before sending.

The `config.yaml` included in this repository contains a specific IP address and AE title. If you deploy a copy of this repository without updating those values, processed patient data — even if anonymized — will be transmitted to that system.

Even with anonymization enabled, the transmitted data contains imaging series, structure sets, and overlay planes. It is not clinically meaningless and should not be transmitted to unintended recipients.

**This is not a bug.** The application works as designed. The risk is entirely in whether the destination is correctly configured before deployment.

### Additional Findings

1. **The DICOM listener accepts connections from any device on your network.**
   Port 11112 is bound to `0.0.0.0` (all interfaces). Standard DICOM does not use passwords or credentials — any machine that can reach the host on that port can send data to CORRECT. Network-level access control (firewall rules, VLAN segmentation) is the only mechanism limiting who can send data to this application.

2. **DICOM data travels over the network without encryption.**
   Inbound transfers from your planning system and outbound transfers to the destination PACS use standard unencrypted DICOM. Data is readable in transit on the network. This is normal for clinical DICOM environments but should be understood.

3. **The anonymization ID-mapping file is stored as unprotected plaintext on disk.**
   `pid_manager.py` writes a file called `pid_mapping.json` to the path configured in `config.yaml` (default: `/mnt/shared/pid_mapping.json`). This file contains a lookup table linking hashed original patient identifiers to anonymized IDs such as `SITE_0001_20240101`. Anyone who can read this file and has access to original patient records can use it to re-identify patients in the anonymized dataset. This file must be treated with the same sensitivity as PHI.

4. **Log files contain patient-linked identifiers.**
   The application logs Study Instance UIDs to `application.log` and `transaction.log`. UIDs are not directly human-readable as patient names, but they can be cross-referenced against a PACS to link a log entry to a specific patient. Log files should not be treated as non-sensitive data.

5. **The application runs as a non-root user inside the container.** *(Positive finding)*
   After startup, the application runs as `appuser`, not root. This limits what a compromised application process could do inside the container.

6. **The configuration file is mounted read-only.** *(Positive finding)*
   `docker-compose.yml` mounts `config.yaml` with the `:ro` flag. The running application cannot modify its own configuration.

---

## Before You Deploy: A Practical Checklist

- [ ] **Verify the destination in `config.yaml`.** Find the `destinations` block and confirm the `host`, `port`, and `ae_title` values point to your intended PACS. Send a test C-ECHO (`echoscu`) to confirm connectivity before transmitting any patient data.
- [ ] **Restrict access to port 11112 using firewall rules.** Only systems that should be sending studies to CORRECT (your treatment planning workstation, simulation CT) should be able to reach this port on the host.
- [ ] **Protect `pid_mapping.json`.** Restrict filesystem permissions on the output directory (the `output` volume mount). Back it up securely. Never include it in data exports, repositories, or file transfers.
- [ ] **Treat the logs directory as sensitive.** Apply access controls and a retention policy to `application.log` and `transaction.log`. Study Instance UIDs in these files are patient-linked.
- [ ] **Confirm network segmentation.** Because the DICOM listener has no built-in authentication, network isolation is the primary access control. Verify the host is not reachable from untrusted network segments.
- [ ] **Windows: install into the default `Program Files` location and prefer the virtual service account.** See *Known limitations* below. A custom install folder can be writable by every user, and LocalSystem gives the service full control of the machine.

---

## Installer and Service Review (2026-11, v0.3.0-alpha.1)

The Windows installer, the Windows service and the Linux packages were reviewed by reading their source code. This was also not a penetration test.

**Fixed in this release**

1. **Data folder takeover on Windows.** A standard user could create `C:\ProgramData\CORRECT` before an administrator ran first-time setup. The user would then own the folder and could restore their own access after setup had locked it down. Setup now gives ownership to Administrators and resets every permission in the folder tree before granting access only to Administrators and SYSTEM. **If `config.yaml` already existed in that folder before your first setup, check its destination and folder paths before you start the service.**
2. **Windows firewall rule open on Public networks.** The inbound rule for the DICOM port applied to all network profiles. It now applies only to the Domain and Private profiles.
3. **`sudo correct-config` could run planted code as root (Linux).** The launcher started Python in a way that searched the current directory first. Starting `sudo correct-config` from a directory holding a planted module ran that module as root. The launcher now runs Python in isolated mode (`-I`).

**Known limitations in this alpha** (accepted for now and tracked for a later release)

- **The Windows service runs as LocalSystem by default.** The service accepts DICOM data from the network, so a flaw in parsing that data would give full control of the machine. If your data folders are on local disks, choose the *virtual account* (`NT SERVICE\CORRECT`) on the Service tab, or install with `install-service --account virtual`.
- **Custom install folders on Windows.** An "all users" install into the default `Program Files` folder is admin-only. Installing into a folder such as `C:\CORRECT` inherits that drive's permissions, which commonly let any signed-in user modify files. The service then runs those files as SYSTEM. Use the default location.
- **"Only for me" installs and administrator approval.** For this install type, the administrator prompt (UAC) launches Python from the user's own profile, which that user can modify. If the person approving the prompt is a different administrator, use an "all users" install.
- **The systemd unit's sandboxing is partial.** `NoNewPrivileges`, `ProtectSystem=full` and `UMask=0027` are set. Further sandboxing options (`ProtectHome`, `PrivateDevices`, `RestrictAddressFamilies`, `SystemCallFilter`) are not set yet.
- **Dependency hashes are not checked at install time.** Package versions are pinned exactly, but pip does not verify their hashes (`--require-hashes`).

**Positive findings**

- The Python runtime and the nFPM packaging tool are verified against pinned SHA256 hashes before they are used.
- Every call to `sc.exe`, `icacls`, `netsh` and `systemctl` passes its arguments as a list (`shell=True` is never used), and the PowerShell commands in the installer contain no user-supplied values.
- Service account passwords are never passed on a command line.
- On Linux, the service runs as a dedicated `correct` system user with no login shell. Data and log folders are `0750 correct:correct`, and `/etc/correct/config.yaml` is `0640 root:correct` and kept on upgrade.
- Uninstall only removes fixed paths and never deletes patient data automatically.
- The Docker image now runs on a supported Debian release (bookworm) and contains only runtime files.

---

## Dependency Security Audit

**Most recent audit: 2026-11-23, release v0.3.0-alpha.1.** The audit covered everything the installers and the Docker image ship, not just `requirements.txt`.

| Scope | Tool | Result before fixes | Result after fixes |
|---|---|---|---|
| Pinned Python packages (`requirements.txt`) | pip-audit 2.10.1 (OSV / PyPI advisory data) | **67 known vulnerabilities, all in pillow 12.2.0** | No known vulnerabilities |
| Python packages as shipped in the .deb/.rpm (34 packages, including pip) | pip-audit 2.10.1 | — | No known vulnerabilities |
| Development dependencies (`pytest`) | pip-audit 2.10.1 | No known vulnerabilities | No known vulnerabilities |
| Bundled CPython (Windows and Linux installers) | python.org security advisories | 3.11.17 | **3.11.18** (security-only release) |
| Docker image | Trivy 0.76.2, HIGH/CRITICAL, fixable only | Base image `python:3.11-slim-bullseye` (Debian 11, end of life) no longer builds | Debian 12 OS packages: 0 findings. Python: 1 false positive (below) |

**Findings and resolutions**

| Finding | Resolution |
|---|---|
| pillow 12.2.0: 67 advisories (25 fixed in 12.3.0, 42 in 13.0.0). CORRECT does not import Pillow directly; it comes in through matplotlib, imageio and scikit-image. | **Fixed:** pinned `pillow==13.0.0`. |
| CPython 3.11.17: `ssl` accepted certificates from compromised eMudhra intermediate CAs (CVE-2026-115557); the bundled expat library had vulnerabilities (CVE-2026-77214, CVE-2026-102633); `tarfile` link filter (CVE-2026-114583); `mimetypes` on Windows (CVE-2024-3220). | **Fixed:** both installers bundle CPython 3.11.18 (python-build-standalone 20261123), with SHA256 hashes taken from the release's `SHA256SUMS`. |
| Docker base image was Debian 11: no further security updates, and `apt-get` fails during the build. | **Fixed:** changed to `python:3.11-slim-bookworm`. |
| The Docker base image ships setuptools 70.3.0 (CVE-2025-47273), with bundled copies of wheel (CVE-2026-24049) and jaraco.context (CVE-2026-23949). | **Fixed:** setuptools and wheel are upgraded while the image is built. |
| The Docker build copied the whole working tree into the image, including `.git`, about 1.3 GB of local build output, and any local `.env` file. | **Fixed:** `.dockerignore` now excludes version control files, secrets, caches, build output, tests and docs. |
| Trivy still reports setuptools 70.3.0 in the image. | **Not applicable:** this entry comes from pip's vendored SBOM (`pip/_vendor/bom.cdx.json`). pip vendors only `pkg_resources` from setuptools, and the vulnerable `package_index` module is not included. |
| `ipaddress==1.0.23` is a Python 2 backport that nothing imports. | **Removed.** (`dataclasses==0.6` is also a no-op on Python 3.11, but rt-utils declares it as a dependency, so it stays pinned.) |
| Debian 12 OS packages: HIGH/CRITICAL advisories with no fix published yet. | **Accepted:** these will be picked up automatically when the image is rebuilt after Debian publishes fixes. |

The current package list is [`requirements.txt`](../requirements.txt). It is not copied here so the two cannot drift apart. To re-run the audit, see the commands in [`SECURITY.md`](../SECURITY.md#how-dependencies-are-audited). Vulnerability databases change daily, so re-run the audit before every release and after any dependency change.

---

## Use at Your Own Risk

This document is a best-effort, informal review. It is not a regulatory compliance assessment, a penetration test, a formal threat model, or a security certification of any kind. Items not evaluated include: the Docker host operating system, network infrastructure, firewall configuration, and destination PACS.

**CORRECT is not FDA-cleared or CE-marked medical device software.** It is a research and workflow utility intended for use by qualified medical physics and informatics professionals who understand DICOM and radiotherapy data.

By deploying and operating this application, you accept full responsibility for:

- Verifying the destination configuration before transmitting any patient data
- Protecting the anonymization mapping file and log files
- Securing the network environment in which the application runs
- Complying with your institution's data governance, HIPAA/privacy, and patient safety requirements

The authors and contributors accept no liability for patient harm, data loss, unauthorized disclosure, regulatory violations, or any other damages resulting from the use of this software.

See [LICENSE](LICENSE) for the full MIT license terms.

---

*Last reviewed: April 2026. This document reflects the state of the codebase at the time of review.*

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

---

## Dependency Security Audit

Python dependencies were audited using [pip-audit](https://github.com/pypa/pip-audit), an open-source tool maintained by the Python Packaging Authority (PyPA). It checks installed packages against the Open Source Vulnerabilities (OSV) database.

```
pip-audit -r requirements.txt
```

### Full Dependency List at Time of Review

| Package | Version | Package | Version |
|---|---|---|---|
| contourpy | 1.3.1 | pyaml | 25.1.0 |
| cycler | 0.12.1 | pydicom | 3.0.2 |
| dataclasses | 0.6 | pyjpegls | 1.4.0 |
| fonttools | 4.60.2 | pynetdicom | 2.0.2 |
| highdicom | 0.24.0 | pyparsing | 3.2.1 |
| imageio | 2.37.0 | python-dateutil | 2.9.0.post0 |
| ipaddress | 1.0.23 | PyWavelets | 1.8.0 |
| kiwisolver | 1.4.8 | PyYAML | 6.0.2 |
| lazy_loader | 0.4 | rt-utils | 1.2.7 |
| matplotlib | 3.10.0 | scikit-image | 0.25.1 |
| networkx | 3.4.2 | scipy | 1.15.1 |
| numpy | 2.2.2 | six | 1.17.0 |
| opencv-python | 4.11.0.86 | tifffile | 2025.1.10 |
| packaging | 24.2 | tqdm | 4.67.1 |
| pillow | 12.2.0 | typing_extensions | 4.12.2 |
| | | watchdog | 6.0.0 |

CVE databases are updated continuously. Re-run `pip-audit -r requirements.txt` periodically and after any dependency change.

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

# Release Notes — CORRECT

Project: **CORRECT — DICOM RT Structure Processor** (`MrRichard/CORRECT`)

---

# v0.3.0-alpha.1 — 2026-11-23 (pre-release)

This is the first build to ship as installers. It is an **alpha** release, intended for evaluation on test systems. Read
[`docs/DISCLAIMER.md`](DISCLAIMER.md) before routing patient data through it.

## Downloads

| File | Platform |
|---|---|
| `CORRECT-Setup-0.3.0-alpha.1.exe` | Windows 10/11 x64. Installs for all users (admin) or just for the current user. |
| `correct_0.3.0~alpha.1-1_amd64.deb` | Debian 10+ / Ubuntu 20.04+ |
| `correct-0.3.0~alpha.1-1.x86_64.rpm` | RHEL / Rocky / Alma 8+, Fedora |
| `SHA256SUMS` | Checksums for the three files above |

Every installer bundles its own Python 3.11.18. No system Python and no internet access are needed on the target machine.

## New

- **Windows installer and native Windows service.** An Inno Setup installer with a private Python runtime. The *CORRECT Configuration* app creates the
  config on first launch and installs, starts, stops and removes the service. The service can run as LocalSystem, a virtual account
  (`NT SERVICE\CORRECT`) or a named user/gMSA. A `service_control` CLI is included for scripted installs. See
  [`installation-windows.md`](installation-windows.md).
- **Self-contained .deb and .rpm packages.** The layout is `/opt/correct`, `/etc/correct/config.yaml` (kept on upgrade),
  `/var/lib/correct` and `/var/log/correct`, with a systemd unit that runs as a dedicated `correct` user. Removing the package keeps patient
  data and the PID mapping. Installing over an `install.sh` or OVA deployment is refused, with migration steps shown. See
  [`installation-linux-packages.md`](installation-linux-packages.md).
- **CI for every installer.** CI builds the .exe, .deb and .rpm. It installs the .exe silently on Windows, runs the service and answers C-ECHO. It
  installs, upgrades and removes the packages on 8 Linux distributions and under systemd. Version tags produce a draft GitHub release.

## Fixes

- Removing the Windows service now works even when the service is stuck in a crash loop, and uninstall failures are shown to the user instead of
  being ignored.
- Version is single-sourced from `netrt_core.__version__` (pyproject previously said 0.1.0).
- `.gitignore` entries for `build/` and `.claude` were merged into one broken line; restored.

## Security

A full audit was done for this release. The details, including accepted limitations, are in
[`DISCLAIMER.md`](DISCLAIMER.md#dependency-security-audit).

- **pillow 12.2.0 → 13.0.0** fixes 67 known vulnerabilities. With that, pip-audit finds no known vulnerabilities in the shipped packages.
- **Bundled CPython 3.11.17 → 3.11.18** (security-only release): fixes in ssl (compromised eMudhra CA keys), expat, tarfile and mimetypes.
- **Docker:** the image moves to Debian 12 (bookworm), because the Debian 11 base no longer builds. setuptools and wheel are upgraded in the image,
  and `.dockerignore` now keeps `.git`, local build output and any `.env` file out of it.
- **Windows:** first-time setup takes ownership of `ProgramData\CORRECT` before locking it down. This blocks a standard user from
  pre-creating that folder. The firewall rule no longer applies to Public networks.
- **Linux:** `sudo correct-config` no longer imports Python modules from the current directory.
- Added [`SECURITY.md`](../SECURITY.md), which covers how to report a vulnerability privately and how to re-run the audit.

## Known limitations (alpha)

- The Windows service defaults to **LocalSystem**. Use the virtual account if your data folders are on local disks.
- On Windows, install into the default `Program Files` folder. A custom folder can inherit permissions that let any user modify it.
- Interactive uninstall on Windows (the dialog path) has not yet been verified by hand. Silent uninstall is covered by CI.
- `rt-utils <= 1.2.7` still needs the runtime shim (`netrt_core/rt_utils_compat.py`).

---

# Earlier changes (2026-10-15 → 2026-10-29, pre-installer)

## 1. Bug fix: rt-utils 1.2.7 `warn_only` NameError

**2b1bfe3 — fix: rt-utils warn_only path raised NameError instead of warning**

`rt-utils==1.2.7` has a real bug: `rt_utils/rtstruct_builder.py` uses `warnings` on its
`warn_only` path without importing it, so instead of emitting a warning it raises a
`NameError`. Upstream fixed it (qurit/rt-utils) but never released a new version.

- Fixed via a **runtime shim** at `netrt_core/rt_utils_compat.py`, which injects stdlib
  `warnings` into `rt_utils.rtstruct_builder` at package import.
- The shim arms only when launching through `correct` / `main.py` (netrt_core first);
  importing `rt_utils` directly still exposes the broken path.
- New regression tests: `tests/test_rtutils_compat.py`.

## 2. GTV series focus (contour target selection)

The processor now explicitly targets GTV series:

- **Optional config** `processing.target_contour_names_containing` — names the contour
  targets on the RTSTRUCT. Default `[“GTV”]`; accepts a single string or a list of
  patterns. Documented in `config.yaml`, `config.pacs-example.yaml`, and
  `docs/CONFIGURATION.md` (27d3fad).
- **No CTV/PTV fallback.** An interim multi-target fallback (GTV → CTV → PTV) was added
  (ce6b788) and then deliberately reverted (b3fad9a); missing target contours are a
  **hard failure**: the study is quarantined and a report written. No silent
  substitution.
- Contour selection logic rewritten in `netrt_core/contour_processor.py` with a
  dedicated test suite `tests/test_contour_gtv_selection.py` (214 lines added, 27d3fad;
  stale test assertion repaired in ac87605).
- Quarantine directory contents are now **cleared on application startup** (b3fad9a).
- `ImplementationVersionName` kept at or under 16 characters (DICOM VR limit) (ce6b788).

## 3. pydicom warning cleanup & listener hardening

- Fixed pydicom deprecation warnings in save/listener paths (2fb9a4c, ce6b788).
- A file **missing `ContourSequence`** is now a warning + skip, not a crash (2fb9a4c).
- Per-file listener events demoted to DEBUG; `INFO` is reserved for
  received / processing / sent (2fb9a4c).

## 4. Packaging & entry point

- **`pyproject.toml`** added (a76967b): `pip install -e .` now works and provides the
  `correct` console command (`main:main`) — also the supported way to arm the rt-utils
  shim.
- README stale local-run entrypoint fixed (`netrt_core/main.py` doesn't exist — the
  entry points are top-level `main.py` / `correct`) (ac87605).

## 5. Installer & updater tooling

New `install/` directory with a full lifecycle (f8dac64):

| Script | Purpose |
|---|---|
| `install.sh` | Debian/Ubuntu + RHEL/Rocky/Fedora single-script install |
| `update.sh` | Generic updater (bare-metal or VM) |
| `update-vm.sh` | OVA/Debian Trixie updater (self-contained) |
| `uninstall.sh` | Removal (`--purge`, `--purge --keep-config`) |

Layout matches the OVA appliance: `/opt/correct/app/{src,venv,config.yaml}`, with the
runtime config **outside** `src/` so pulls preserve it.

Fixes to the tooling, driven by real deployment attempts:

- **Fedora 43** (f6400f8): pip package discovery, privilege drop via `runuser` (not
  `sudo -u` — HOME leak), `--source PATH` install option, pip bootstrap. Fedora ships
  Python 3.14 (no wheels for the pinned stack), so the installer auto-selects
  `python3.13`.
- **OVA VMs** (13d74a4): updaters normalize the root-owned venv
  (`chown -R correct:correct <venv> <src>`) before pip steps — fixes
  `Permission denied (Errno 13)` on pip during VM updates.
- **GROUP_NAME under `set -u`** (1a62e30): variable was unbound/nonexistent in the
  updaters.
- **Ubuntu** (86d6277): get-pip.py bootstrap permission fix (root-owned mktemp file)
  and explicit `command -v python3.11` verification so 22.04 doesn't silently fall
  back to the 3.10 default.

## 6. Documentation

- **README rewritten** (f8dac64) with the three deployment paths and complete install /
  update / uninstall guidance; local (Python 3.11) run mode added (fa9904d).
- **`docs/VM_UPDATE_GUIDE.md`** (d851d49): manual procedure for updating VMs while
  preserving local configuration.
- **`docs/installation-fedora43.md`** (f8dac64): Fedora 43 install walkthrough.
- **First-time VM bootstrap** added to README (1a4178e): one-time `git pull` for VMs
  built before `install/` existed, after which `update-vm.sh` is self-contained.

---

## Tests

15 test functions across three suites (`tests/test_rtutils_compat.py`,
`tests/test_contour_gtv_selection.py`, `tests/test_contour_processor_warning.py`),
covering the rt-utils shim, GTV selection (string/list targets, missing-target
hard failure), and contour-processor warning behavior. The previously stale assertion
in the GTV suite was repaired (ac87605).

## Commit index (newest first)

| Date | Hash | Summary |
|---|---|---|
| 2026-10-29 | 86d6277 | fix(install): Ubuntu get-pip.py permission + python3.11 selection |
| 2026-10-28 | 1a4178e | docs(README): add first-time VM bootstrap step |
| 2026-10-28 | 1a62e30 | fix(update): GROUP_NAME unbound under set -u in updaters |
| 2026-10-28 | 13d74a4 | fix(update): normalize root-owned OVA venv before pip steps |
| 2026-10-28 | f6400f8 | fix(install): Fedora pip package, runuser HOME leak, --source option, pip bootstrap |
| 2026-10-28 | f8dac64 | feat: add installer/updater/uninstaller tooling + rewrite README |
| 2026-10-28 | f4a1fbb | Merge fix/pydicom-and-contour-sequence into master |
| 2026-10-28 | ac87605 | fix: correct README local-run entrypoint and stale GTV test assertion |
| 2026-10-26 | d851d49 | docs: add VM update guide for preserving local config on pull |
| 2026-10-26 | a76967b | Add pyproject.toml so pip install -e . works |
| 2026-10-26 | b3fad9a | Revert GTV fallback; optional target contour config (string or list); quarantine cleanup on startup |
| 2026-10-26 | ce6b788 | Fallback GTV->CTV->PTV targets; shorten ImplementationVersionName to <=16; additional pydicom noise fixes |
| 2026-10-26 | fa9904d | docs: add local (Python 3.11) run mode to README |
| 2026-10-26 | 27d3fad | Carry over in-progress rtstruct warning and GTV selection changes |
| 2026-10-26 | 2fb9a4c | Fix pydicom deprecation warnings and graceful missing ContourSequence; reduce per-file INFO noise |
| 2026-10-20 | 2b1bfe3 | fix: rt-utils warn_only path raised NameError instead of warning |
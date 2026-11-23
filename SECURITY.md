# Security Policy

CORRECT handles DICOM radiotherapy data on clinical and research networks. Before deploying it,
read [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md), which covers the security evaluation, the risks and
a pre-deployment checklist.

## Supported versions

| Version | Status | Security fixes |
|---|---|---|
| 0.3.0-alpha.x | Pre-release (Windows installer, .deb/.rpm packages) | Yes, in the next alpha |
| 0.2.x and earlier (git / Docker installs) | Superseded | Best effort; upgrade instead |

Alpha releases are for evaluation. Do not route production patient data through an alpha
build without your institution's own review.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub:
**[Security → Report a vulnerability](https://github.com/MrRichard/CORRECT/security/advisories/new)**.

Please include:
- the affected version (`netrt_core.__version__`, or the installer or package filename),
- the platform (Windows installer, .deb/.rpm, Docker, or source),
- steps to reproduce and the impact you expect.

You should get an acknowledgement within 7 days. Fixes ship as a new release, and the
GitHub release notes credit the reporter unless they ask not to be credited.

## How dependencies are audited

Each release candidate is checked for known vulnerabilities in everything the installers ship:

| What | Tool | Command |
|---|---|---|
| Python packages (pinned) | [pip-audit](https://github.com/pypa/pip-audit) | `pip-audit -r requirements.txt` |
| Python packages as shipped | pip-audit | `build/linux/stage/opt/correct/runtime/bin/python3 -m pip list --format=freeze > staged.txt && pip-audit -r staged.txt --no-deps` |
| Bundled CPython runtime | python.org security advisories | compare `PYTHON_VERSION` in `packaging/*/runtime.lock` with the newest 3.11.x |
| Docker image (OS + Python) | [Trivy](https://github.com/aquasecurity/trivy) | `docker build -t correct:scan . && trivy image --severity HIGH,CRITICAL --ignore-unfixed correct:scan` |

The results of the most recent audit, and how each finding was handled, are recorded in
[`docs/DISCLAIMER.md`](docs/DISCLAIMER.md#dependency-security-audit). Re-run the audit before every
release and after any dependency change. Vulnerability databases change daily, so a clean result is
only valid on the date it was produced.

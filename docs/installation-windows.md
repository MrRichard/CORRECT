# Installing CORRECT on Windows (native service)

CORRECT runs as a native Windows service on Windows Server 2016 and later, and on Windows 10/11.
The installer sets it up for **all users**. The service can log on as **LocalSystem**, as the
**`NT SERVICE\CORRECT`** virtual account, or as a **user or gMSA** you choose. Docker is not involved.

## Requirements

- 64-bit Windows, with an Administrator account to install
- Internet access during install. The installer downloads Python 3.11.9 if it is missing, and it
  downloads the pinned Python packages from PyPI.
- A data disk with room for in-flight studies. Keep the data root short, because Windows paths
  are limited to 260 characters and DICOM UIDs are long.

## Install

1. Copy or clone this repository onto the server, for example to `C:\Temp\CORRECT`.
2. Double-click `install\windows\setup.cmd`, or run it from an elevated prompt with options:

   ```bat
   install\windows\setup.cmd                                      :: LocalSystem, port 11112
   install\windows\setup.cmd --account virtual                    :: NT SERVICE\CORRECT
   install\windows\setup.cmd --account user --user CONTOSO\svc_correct
   install\windows\setup.cmd --account user --user CONTOSO\correct-gmsa$
   install\windows\setup.cmd --config D:\site\config.yaml --port 11112
   install\windows\setup.cmd --data-dir D:\CORRECT                :: data on another disk
   install\windows\setup.cmd --dry-run                            :: print steps, change nothing
   install\windows\setup.cmd --help
   ```

   `setup.cmd` runs these steps:

   - It asks for elevation (UAC).
   - It looks for an **all-users** Python 3.11. If there isn't one, it downloads
     `python-3.11.9-amd64.exe` from python.org, **checks its SHA256**, and installs it silently
     with `InstallAllUsers=1 PrependPath=0`.
   - It runs `install_windows.py` with that Python.

   For `--account user`, the installer asks for the password. The password never goes on the
   command line. A gMSA account (name ends in `$`) needs no password.

3. Set `dicom_destination`, `anonymization.site_code` and the other site values in the config,
   using **Start Menu → CORRECT → CORRECT Configuration**. Then click **Save & Restart service**.

### What gets installed

| Path / item | Purpose |
|---|---|
| `C:\Program Files\CORRECT\app\src` | Application source |
| `C:\Program Files\CORRECT\app\venv` | Python virtualenv, including `pythonservice.exe` |
| `C:\ProgramData\CORRECT\config.yaml` | Runtime config, kept across re-installs |
| `C:\ProgramData\CORRECT\working` | Incoming studies and quarantine |
| `C:\ProgramData\CORRECT\logs` | `application.log`, `transaction.log` |
| `C:\ProgramData\CORRECT\shared` | Processing reports and `pid_mapping.json` |
| Service `CORRECT` | Starts automatically (delayed). Restarts on failure after 10 s, 10 s, then 60 s |
| Firewall rule `CORRECT DICOM` | Allows inbound TCP on the listener port |
| Start Menu → CORRECT | **CORRECT Configuration** (config UI) and **CORRECT Logs** |
| Settings → Apps | Entry named "CORRECT DICOM RT Structure Processor", used to uninstall |

`C:\ProgramData\CORRECT` may hold PHI, so the installer turns off permission inheritance on it.
Only **Administrators**, **SYSTEM** and the **service account** can access it.

## Choosing the service account

| Account | When to use |
|---|---|
| **LocalSystem** (default) | Simplest setup. Has full local rights, and on the network it acts as the computer account (`DOMAIN\HOST$`). |
| **`NT SERVICE\CORRECT`** (virtual) | Recommended when all paths are on local disks. Least privilege, no password to manage. |
| **User or gMSA** | Use this when `pid_mapping_file` or the report directory is on a **network share (UNC path)**. Grant the account rights on the share and its folder. A gMSA (`DOMAIN\name$`) avoids password rotation. |

The installer and the config UI both do the following for a named user:

- grant the **Log on as a service** right
- give the account Modify on the configured working, log, report and PID-mapping folders

ACLs on UNC shares are left alone; manage those on the file server.

To change the account later, open **CORRECT Configuration → Service**, pick the account and
click **Apply account & restart**. From the command line, run this in an elevated prompt:

```bat
"C:\Program Files\CORRECT\app\venv\Scripts\python.exe" -m correct_admin.service_control set-account --account virtual
"C:\Program Files\CORRECT\app\venv\Scripts\python.exe" -m correct_admin.service_control acl --writable C:\ProgramData\CORRECT\working C:\ProgramData\CORRECT\logs C:\ProgramData\CORRECT\shared --readable C:\ProgramData\CORRECT
sc stop CORRECT && sc start CORRECT
```

## Configuration

There are two ways to edit the config:

- **GUI:** Start Menu → CORRECT → **CORRECT Configuration**. It asks for UAC elevation.
  - It validates fields: ports, AE titles of 16 characters or fewer, hosts, absolute paths.
  - It keeps comments in the YAML and writes `config.yaml.bak` before every save.
  - **Test C-ECHO** checks that the destination is reachable.
  - The **Service** tab starts, stops and restarts the service and changes its account.
- **By hand:** edit `C:\ProgramData\CORRECT\config.yaml` as Administrator, then restart the
  service with `sc stop CORRECT` and `sc start CORRECT`, or from `services.msc`.
  Single-quote Windows paths so backslashes are kept literally:
  `working: 'D:\CORRECT\working'`.

**Server Core** has no desktop, so edit the YAML by hand. The service itself is not affected.

If the working directory is on a network share, set the system environment variable
`WATCHDOG_USE_POLLING=true`, because change notifications are unreliable over SMB. A local
working directory is strongly recommended.

## Operating

| Task | Command |
|---|---|
| Status | `sc query CORRECT` or `services.msc` |
| Start / stop | `sc start CORRECT` / `sc stop CORRECT` |
| Logs | `C:\ProgramData\CORRECT\logs\application.log`; service start/stop/errors in **Event Viewer → Windows Logs → Application** (source `CORRECT`) |
| Listener check | `netstat -ano \| findstr :11112` |
| Debug in console | `"C:\Program Files\CORRECT\app\venv\Scripts\python.exe" -m correct_admin.winservice debug` (stop the service first) |

## Updating

Get the new version of the repository and run `install\windows\setup.cmd` again. The installer is
idempotent: it stops the service, replaces the source, refreshes dependencies, re-registers the
service, and starts it again. Config and data are **kept**.

## Uninstalling

Use **Settings → Apps → CORRECT DICOM RT Structure Processor → Uninstall**, or run:

```bat
python "C:\Program Files\CORRECT\app\src\install\windows\uninstall_windows.py"          :: keeps ProgramData\CORRECT
python "C:\Program Files\CORRECT\app\src\install\windows\uninstall_windows.py" --purge  :: also deletes config + data
```

## Troubleshooting

- **Service starts, then stops right away.**
  - Check Event Viewer (Application log) and `application.log`.
  - Run the `debug` command shown under Operating to see the error in a console.
- **Error 1069 (logon failure).** The password is wrong, or the account lacks the
  **Log on as a service** right. Re-apply the account from the config UI.
- **"Access is denied" writing to a share.** LocalSystem and the virtual account reach the
  network as the computer account. Use a domain user or gMSA that has rights on the share.
- **Python packages fail to install.** The server needs HTTPS access to `pypi.org` and
  `files.pythonhosted.org`, or a proxy configured through the `HTTPS_PROXY` environment variable.

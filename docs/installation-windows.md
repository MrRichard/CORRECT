# Installing CORRECT on Windows

CORRECT runs as a native Windows service on Windows Server 2016 and later, and on Windows 10/11
(64-bit). Docker is not involved.

Installation has two stages:

1. **`CORRECT-Setup-<version>.exe`** installs the software only: a private copy of Python with
   every package CORRECT needs, plus the CORRECT program files. It needs no internet access and
   doesn't use or change any Python already on the machine.
2. The **CORRECT Configuration** app, launched at the end of setup or from the Start Menu, creates
   the configuration and then **installs and manages the service**.

## Getting the installer

- **Releases:** download `CORRECT-Setup-<version>.exe` from the project's GitHub *Releases* page.
- **Development builds:** go to GitHub → *Actions* → *Windows installer*, open a run, and download
  the **CORRECT-Setup** artifact.
- **Build it yourself on Windows:** install Inno Setup 6 (`winget install JRSoftware.InnoSetup`),
  then run `powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1`. The installer
  is written to `dist\`.

The installer isn't code-signed yet. Windows SmartScreen will warn about an *unknown publisher*;
choose **More info → Run anyway**.

## Choose an install mode

Setup asks whether to install **for all users** or **for me only**:

| | **All users** (recommended for servers) | **Only for me** |
|---|---|---|
| Needs admin to install | Yes | No |
| Program files | `C:\Program Files\CORRECT` | `%LOCALAPPDATA%\Programs\CORRECT` |
| Config and data | `C:\ProgramData\CORRECT` | `%LOCALAPPDATA%\CORRECT` |
| Service logs on as | **Local System** (default). Advanced options: the virtual account `NT SERVICE\CORRECT`, or a specific user or gMSA | **You**, your own Windows account |
| Admin needed later | To change settings or control the service | Once, to install or remove the service. After that you can start, stop and reconfigure without admin |

`C:\ProgramData\CORRECT` may contain PHI, so in all-users mode it is locked down to
Administrators, SYSTEM and the service account.

## First run: configure, then install the service

When setup finishes, leave **Launch CORRECT Configuration** ticked. You can also open it later
from **Start Menu → CORRECT → CORRECT Configuration**. In all-users mode it asks for UAC elevation.

On first launch the app creates the data folders and a starting `config.yaml`. Then:

1. **Destination tab:** enter the PACS/VNA host, port and AE title. Click **Test C-ECHO** to check
   that it's reachable.
2. **Anonymization tab:** set your **site code**. Check the other tabs too: listener port, contour
   targets, features.
3. Click **Save**. The app won't save placeholder values (`YOUR_PACS_IP` and so on) or invalid
   ports, AE titles or paths. Comments in the file are kept, and a `config.yaml.bak` is written
   before every save.
4. **Service tab:** leave **Allow the DICOM listener port through Windows Firewall** ticked, then
   click **Install service**:
   - **All users:** choose the account (Local System, unless your data is on a network share) and
     click **Install service**.
   - **Only for me:** approve the UAC prompt, then enter **your Windows password** in the prompt
     that follows. Windows needs it to run the service as you. The password is checked
     immediately and stored by Windows, not by CORRECT. A service can't log on to an account
     that has no password.

The service then starts. It starts automatically at boot (delayed start), and Windows restarts it
if it crashes.

## Managing the service afterwards

You can manage the service in either place:

- **Services console (`services.msc`):** look for **CORRECT DICOM RT Structure Processor**.
  Start, stop, startup type and recovery all work as usual.
- **CORRECT Configuration → Service tab:** status, Start / Stop / Restart, Remove service, and
  (all users) **Apply account & restart** to switch accounts.

After changing settings, click **Save & Restart service**. If you change the listener port, the
firewall rule is updated to match.

**Choosing an account (all users):**

| Account | Use when |
|---|---|
| Local System | Default. Simplest choice. On the network it acts as the computer account. |
| `NT SERVICE\CORRECT` | You want least privilege and all paths are on local disks. |
| A user or gMSA (`DOMAIN\name$`) | `pid_mapping_file` or the report directory is on a **network share**. Give that account rights on the share. |

For a named user, the app grants **Log on as a service** and gives the account Modify on the
configured local folders. Permissions on network shares are managed on the file server.

**Only-for-me installs:** if your Windows password changes, the service can no longer log on.
Click **Remove service**, then **Install service** again.

## Headless servers / Server Core

```bat
CORRECT-Setup-x.y.z.exe /VERYSILENT /ALLUSERS
set PY="C:\Program Files\CORRECT\runtime\python.exe"
%PY% -m correct_admin.service_control init-config
notepad C:\ProgramData\CORRECT\config.yaml
%PY% -m correct_admin.service_control install-service --scope machine --account localsystem
%PY% -m correct_admin.service_control status
```

Other commands:

- `start`, `stop`, `restart`
- `set-account --account virtual`
- `set-account --account user --user DOMAIN\name`, which prompts for the password
- `firewall --port N`
- `remove-service`

## Upgrading

Run the newer `CORRECT-Setup-<version>.exe` and choose the same install mode. Setup:

1. stops the service
2. replaces the program files and Python packages
3. starts the service again

Your configuration and data are not touched.

## Uninstalling

Use **Settings → Apps → CORRECT DICOM RT Structure Processor → Uninstall**, or
**Start Menu → CORRECT → Uninstall CORRECT**. The uninstaller:

1. stops and removes the service and its firewall rule (an only-for-me install asks for UAC
   approval for this)
2. removes the program files
3. asks whether to also delete the configuration and data. The default is **No**, so a later
   reinstall picks up where you left off. A silent uninstall (`/VERYSILENT`) always keeps them.

## Logs and troubleshooting

| Where | What |
|---|---|
| `<data>\logs\application.log` | Application log. Use **Start Menu → CORRECT → CORRECT Data and Logs** |
| `<data>\logs\transaction.log` | DICOM associations and transfers |
| Event Viewer → Windows Logs → Application | Service start/stop and startup failures (source `CORRECT`) |

- **Service starts, then stops.** Check the Event Viewer and `application.log`. To see the error
  in a console, stop the service and run:
  `"<install dir>\runtime\python.exe" -m correct_admin.winservice debug`
- **Error 1069 (logon failure).** The password is wrong or has changed. Reinstall the service
  (only for me), or re-apply the account (all users).
- **"Access is denied" on a network share.** Local System and the virtual account reach the
  network as the computer account. Use a domain user or gMSA that has rights on the share.
- **Working directory on a network share:** set the system environment variable
  `WATCHDOG_USE_POLLING=true`. A local working directory is strongly recommended.
- **Long paths:** Windows paths are limited to 260 characters, so keep the data folder short.

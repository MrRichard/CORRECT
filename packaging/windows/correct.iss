; CORRECT Windows installer (Inno Setup 6). Built by packaging\windows\build.ps1:
;   ISCC.exe /DAppVersion=x.y.z /DStageDir=<build\windows\stage> /DOutputDir=<dist> correct.iss
;
; Installs files only: a private Python runtime (runtime\) and the CORRECT source (app\).
; It does NOT create the config or the service — the CORRECT Configuration app does that
; on first launch. The install mode (all users / current user) is recorded in install.json.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\..\build\windows\stage"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{8C3B5E2A-6E1F-4C55-9E0B-6B3A2D1C9F47}
AppName=CORRECT
AppVersion={#AppVersion}
AppVerName=CORRECT {#AppVersion}
AppPublisher=CORRECT
AppPublisherURL=https://github.com/MrRichard/CORRECT
AppSupportURL=https://github.com/MrRichard/CORRECT/issues
UninstallDisplayName=CORRECT DICOM RT Structure Processor
UninstallDisplayIcon={app}\runtime\pythonw.exe
DefaultDirName={autopf}\CORRECT
DefaultGroupName=CORRECT
DisableProgramGroupPage=yes
; "Install for all users" (admin, Program Files) or "Install for me only" (no admin).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
UsePreviousPrivileges=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#StageDir}\app\LICENSE.txt
InfoBeforeFile={#StageDir}\app\DISCLAIMER.md
OutputDir={#OutputDir}
OutputBaseFilename=CORRECT-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
SetupLogging=yes
CloseApplications=no

[Messages]
FinishedLabel=CORRECT has been installed.%n%nNext, open CORRECT Configuration to enter your site settings and install the CORRECT service.

[InstallDelete]
; Upgrades replace code and packages wholesale (no stale modules). Config and data are untouched.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#StageDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\CORRECT\CORRECT Configuration"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m correct_admin.config_ui"; WorkingDir: "{app}\app"; Comment: "Configure CORRECT and install or manage its service"
Name: "{autoprograms}\CORRECT\CORRECT Data and Logs"; Filename: "{code:GetDataDir}"
Name: "{autoprograms}\CORRECT\Uninstall CORRECT"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m correct_admin.config_ui"; WorkingDir: "{app}\app"; Description: "Launch CORRECT Configuration"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop and delete the service + firewall rule. For a current-user install this asks for
; Administrator approval, but only if the service was actually installed.
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m correct_admin.service_control --elevate-if-needed remove-service"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveCorrectService"

[UninstallDelete]
; Python writes __pycache__ at runtime; remove everything we put under {app}.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\install.json"
Type: dirifempty; Name: "{app}"

[Code]
var
  ServiceWasRunning: Boolean;

function GetDataDir(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := ExpandConstant('{commonappdata}\CORRECT')
  else
    Result := ExpandConstant('{localappdata}\CORRECT');
end;

function JsonEscape(S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure WriteInstallInfo();
var
  Scope: String;
  Lines: TArrayOfString;
begin
  if IsAdminInstallMode then Scope := 'machine' else Scope := 'user';
  SetArrayLength(Lines, 5);
  Lines[0] := '{';
  Lines[1] := '  "scope": "' + Scope + '",';
  Lines[2] := '  "data_dir": "' + JsonEscape(GetDataDir('')) + '",';
  Lines[3] := '  "version": "{#AppVersion}"';
  Lines[4] := '}';
  // UTF-8 so non-ASCII profile paths (C:\Users\José\...) survive; the reader accepts a BOM.
  SaveStringsToUTF8File(ExpandConstant('{app}\install.json'), Lines, False);
end;

function RunPowerShell(Command: String): Integer;
var
  Code: Integer;
begin
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Command + '"',
              '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Result := Code;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  // Upgrade: stop a running service so its files are not locked (exit 10 = it was running).
  // Current-user installs were granted stop rights on the service, so this works without admin.
  ServiceWasRunning := RunPowerShell(
    '$s = Get-Service -Name CORRECT -ErrorAction SilentlyContinue; ' +
    'if ($s -and $s.Status -ne ''Stopped'') { Stop-Service -Name CORRECT -Force -ErrorAction Stop; ' +
    '$s.WaitForStatus(''Stopped'', ''00:02:00''); Start-Sleep -Seconds 2; exit 10 } else { exit 0 }') = 10;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteInstallInfo();
    if ServiceWasRunning then
      RunPowerShell('Start-Service -Name CORRECT');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := GetDataDir('');
    if (not UninstallSilent) and DirExists(DataDir) then
      if MsgBox('Also delete the CORRECT configuration and all data in' + #13#10 + DataDir + '?' + #13#10#13#10 +
                'This includes the PID mapping, logs, reports and any studies still being processed ' +
                '(may contain patient data). Choose No to keep them for a later reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;

@echo off
rem CORRECT Windows installer launcher.
rem Double-click, or run from an elevated prompt with options, e.g.:
rem   setup.cmd --account virtual --port 11112
rem   setup.cmd --account user --user CONTOSO\svc_correct
rem   setup.cmd --dry-run
rem Run "setup.cmd --help" for all options.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set RC=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %RC%

@echo off
setlocal
if not exist "%~dp0.runtime\powershell\pwsh.exe" (
  echo Please extract the complete ZIP before starting the application.
  pause
  exit /b 1
)
"%~dp0.runtime\powershell\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\portable-start.ps1" %*
if errorlevel 1 (
  echo The application could not start. Please read the error above.
  pause
  exit /b 1
)

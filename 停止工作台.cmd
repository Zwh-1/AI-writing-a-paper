@echo off
setlocal
if not exist "%~dp0.runtime\powershell\pwsh.exe" (
  echo Please extract the complete ZIP first.
  pause
  exit /b 1
)
"%~dp0.runtime\powershell\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\portable-start.ps1" -Stop %*
if errorlevel 1 (
  echo The application could not stop. Please read the error above.
  pause
  exit /b 1
)

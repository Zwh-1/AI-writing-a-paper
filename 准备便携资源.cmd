@echo off
setlocal
if not exist "%~dp0.runtime\powershell\pwsh.exe" (
  echo Please extract the complete ZIP before preparing resources.
  pause
  exit /b 1
)
"%~dp0.runtime\powershell\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\portable-start.ps1" -PrepareResources
if errorlevel 1 (
  echo Resource preparation failed. Check your network and run this file again.
  pause
  exit /b 1
)
echo All resources are ready. No additional component packages are needed.
pause

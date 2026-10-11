@echo off
REM === One-click launcher (ASCII only) ===
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Startup script failed. See messages above.
  pause
)
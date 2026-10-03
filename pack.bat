@echo off
setlocal enableextensions
cd /d "%~dp0"

echo ==========================================
echo  BaoMi Agent - build deploy archive
echo ==========================================
echo.

set "OUT=%~dp0baomi-deploy.tar.gz"
set "TMPOUT=%TEMP%\baomi-deploy.tar.gz"

if exist "%TMPOUT%" del /f /q "%TMPOUT%"

where tar >nul 2>nul
if errorlevel 1 (
  echo [ERROR] tar.exe not found.
  echo         Windows 10 version 1803 and later include it by default.
  echo         Alternative: open Git Bash here and run:  bash pack.sh
  echo.
  pause
  exit /b 1
)

echo [1/2] packing source tree ...
echo        excluded: node_modules .next .git chroma_db data .workbuddy ui-redesign venv .env
tar -czf "%TMPOUT%" --exclude=node_modules --exclude=.next --exclude=.git --exclude=chroma_db --exclude=data --exclude=.workbuddy --exclude=.trae --exclude=ui-redesign --exclude=baomi-agent --exclude=venv --exclude=.venv --exclude=__pycache__ --exclude=*.pyc --exclude=*.log --exclude=.env --exclude=baomi-deploy.tar.gz -C "%~dp0" .

if not exist "%TMPOUT%" (
  echo.
  echo [ERROR] tar failed, archive was not created.
  pause
  exit /b 1
)

echo [2/2] moving archive to project folder ...
if exist "%OUT%" del /f /q "%OUT%"
move /y "%TMPOUT%" "%OUT%" >nul

echo.
echo ==========================================
echo  DONE
echo ==========================================
echo  File: %OUT%
echo.
echo Next step, on your Linux server:
echo   mkdir -p /root/baomiagent
echo   tar -xzf baomi-deploy.tar.gz -C /root/baomiagent
echo   cd /root/baomiagent
echo   bash deploy.sh
echo.
pause

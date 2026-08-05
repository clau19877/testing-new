@echo off
setlocal
cd /d "%~dp0"

if exist "dist\PBandaiHK.exe" (
  "dist\PBandaiHK.exe"
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 launch.py
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python launch.py
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)

echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
echo Enable "Add python.exe to PATH" during setup.
pause
exit /b 1

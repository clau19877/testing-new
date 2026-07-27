@echo off
setlocal
cd /d "%~dp0"

REM One-click: setup deps + start monitor loop
if exist "dist\PBandaiHK.exe" (
  "dist\PBandaiHK.exe" --one-click
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 launch.py --one-click
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python launch.py --one-click
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)

echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
pause
exit /b 1

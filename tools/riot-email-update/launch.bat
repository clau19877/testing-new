@echo off
REM One-click Windows launcher (no .exe required).
REM Double-click or run from a console — same behavior as RiotEmailUpdate.exe.
cd /d "%~dp0"
where py >nul 2>&1 && (
  py -3 launch.py %*
  goto :done
)
where python >nul 2>&1 && (
  python launch.py %*
  goto :done
)
where python3 >nul 2>&1 && (
  python3 launch.py %*
  goto :done
)
echo Python 3 was not found. Install from https://www.python.org/downloads/
echo Check "Add python.exe to PATH", then run this again.
pause
exit /b 1
:done
echo.
pause

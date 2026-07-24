# Windows one-click launch

## Quick start

1. Install [Python 3.11+](https://www.python.org/downloads/) once  
   (check **Add python.exe to PATH** and **Install py launcher**).
2. Put `RiotEmailUpdate.exe` in this folder (same place as `launch.py`).
3. Double-click **`RiotEmailUpdate.exe`**.
4. On first run it creates `data/tasks.csv` and opens it — fill the rows, save, run again.
5. A real Chromium window opens; solve each hCaptcha when prompted. MFA + email change stay automated.

Optional: copy `.env.example` → `.env` and add `data/proxies.txt` if you use proxies.
Set `EMAIL_CHANGE_URL` in `.env` (or the `email_change_url` CSV column) if you have a direct Riot email-change page link.

## Alternatives

| File | When to use |
|------|-------------|
| `RiotEmailUpdate.exe` | Double-click on Windows |
| `launch.bat` | Same flow without the .exe |
| `launch.py` | `py -3 launch.py` / `python launch.py` |
| `launch.sh` | macOS / Linux |

## Rebuild the .exe

Needs Go 1.22+:

```bash
./build_windows_exe.sh
```

Output: `RiotEmailUpdate.exe` (Windows amd64). The binary only finds Python and runs `launch.py` — it does not embed secrets or your CSV.

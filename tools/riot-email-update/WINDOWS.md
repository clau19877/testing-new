# Windows one-click launch

## Quick start

1. Install [Python 3.11+](https://www.python.org/downloads/) once  
   (check **Add python.exe to PATH** and **Install py launcher**).
2. Put `RiotEmailUpdate.exe` in this folder (same place as `launch.py`).
3. Double-click **`RiotEmailUpdate.exe`**.
4. On first run it creates `data/tasks.csv` and opens it — fill the rows, save, run again.
5. Default captcha is **in-bot AI** (`vision` / hybrid). Put `TWOCAPTCHA_API_KEY`
   in `.env` (optional `YESCAPTCHA_API_KEY`). MFA + email change stay automated.
   For human solving instead: `CAPTCHA_PROVIDER=manual` + `HEADED=true`.

Optional: copy `.env.example` → `.env` and add `data/proxies.txt` if you use proxies.

Login opens `https://account.riotgames.com/` directly, then fills the Riot
sign-in form. After login, the tool opens `EMAIL_CHANGE_URL` to edit the email.

During manual captcha the browser starts maximized and the challenge popup is
pinned/enlarged so the **full hCaptcha tile table** is visible. Tune with
`HCAPTCHA_VIEW_SCALE=1.4` in `.env` if you still need it larger.

Each run writes a full session log under `debug/logs/` (console + navigations).
Send that file when something fails so the flow can be improved.

### Headless
```bat
RiotEmailUpdate.exe --headless
```
or set `HEADED=false` in `.env`.

Default is in-bot AI (`CAPTCHA_PROVIDER=vision`). Works headed or headless.
Manual mode still needs `HEADED=true`.

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

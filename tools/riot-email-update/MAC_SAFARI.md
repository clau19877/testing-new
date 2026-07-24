# Mac Safari flow (no captcha locally)

Riot often skips hCaptcha in a real Safari session on your Mac. Use this
instead of the Playwright/vision path when running locally.

## One-time Safari setup

1. Safari → **Settings** → **Advanced** → enable **Show Develop menu**
2. **Develop** → enable **Allow JavaScript from Apple Events**

## Run

```bash
cd tools/riot-email-update
cp .env.example .env   # if needed — set RIOT_USERNAME, RIOT_PASSWORD, NEW_EMAIL, IMAP_*
chmod +x run_safari_mac.sh
./run_safari_mac.sh
```

Login-only:

```bash
./run_safari_mac.sh --skip-email-change
```

Or call AppleScript directly:

```bash
osascript safari_riot_email.applescript \
  --username 'RiotUser' \
  --password 'Secret' \
  --new-email 'new@icloud.com'
```

## What it does

1. Opens the docs.qq.com entry link in Safari  
2. Clicks **Continue** → Riot login  
3. Fills username/password and signs in  
4. If MFA appears: pulls a code via IMAP (`.env`) or prompts you  
5. Opens account settings and fills the new email (confirm in Safari)  
6. Optional verify code via IMAP / prompt  

If hCaptcha still appears, the script pauses so you can solve it in Safari.

## Batch from CSV → success.txt / failed.txt

CSV format (same as `data/tasks.csv.example`):

```csv
riot_username,riot_password,imap_email,imap_app_password,new_email,imap_host,imap_port,proxy_index,email_change_url
RiotUser,YourRiotPass,you@icloud.com,xxxx-xxxx-xxxx-xxxx,new@icloud.com,imap.mail.me.com,993,,
```

On your Mac:

```bash
cd tools/riot-email-update
# put rows in data/tasks.csv
chmod +x run_safari_batch.sh
./run_safari_batch.sh data/tasks.csv
```

Results (appended, tab-separated):

| File | Contents |
|------|----------|
| `success.txt` | `username  password  imap_email  imap_app_password  new_email  imap_host` |
| `failed.txt`  | same fields + `reason` |

Dry-run (validate CSV only):

```bash
python3 run_safari_batch.py data/tasks.csv --dry-run
```

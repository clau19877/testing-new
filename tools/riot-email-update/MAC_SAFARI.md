# Mac Safari flow (no captcha locally)

Riot often skips hCaptcha in a real Safari session on your Mac. Use this
instead of the Playwright/vision path when running locally.

## One-time Safari setup

1. Safari → **Settings** → **Advanced** → enable **Show Develop menu**
2. **Develop** → enable **Allow JavaScript from Apple Events**

## Batch from CSV (recommended — no username prompts)

Put accounts in `data/tasks.csv`, then:

```bash
cd tools/riot-email-update   # or the unzipped folder
chmod +x run_safari_mac.sh run_safari_batch.sh fetch_riot_imap_code.py
./run_safari_mac.sh data/tasks.csv
# same as:
./run_safari_batch.sh data/tasks.csv
```

If `data/tasks.csv` already has rows, plain `./run_safari_mac.sh` also runs batch.

CSV columns:

```csv
riot_username,riot_password,imap_email,imap_app_password,new_email,imap_host,imap_port,proxy_index,email_change_url
RiotUser,YourRiotPass,you@icloud.com,xxxx-xxxx-xxxx-xxxx,new@icloud.com,imap.mail.me.com,993,,
```

Results (appended, tab-separated):

| File | Contents |
|------|----------|
| `success.txt` | `username  password  imap_email  imap_app_password  new_email  imap_host` |
| `failed.txt`  | same fields + `reason` |

## Single account (.env / flags)

Only if you are **not** using a CSV:

```bash
cp .env.example .env   # set RIOT_USERNAME, RIOT_PASSWORD, NEW_EMAIL, IMAP_*
./run_safari_mac.sh --username 'RiotUser' --password 'Secret' --new-email 'new@icloud.com'
```

If you run the AppleScript with no args / empty `.env`, it will **prompt** for username — that is expected. Use the CSV batch path above to avoid prompts.

## What it does

1. Opens the docs.qq.com entry link in Safari  
2. Clicks **Continue** → Riot login  
3. Fills username/password from CSV (or args) and signs in  
4. If MFA appears: pulls a code via IMAP or prompts you  
5. Opens account settings and fills the new email  
6. Optional verify code via IMAP / prompt  

If hCaptcha still appears, batch mode fails that row; interactive mode pauses for you to solve it.

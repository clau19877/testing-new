# Mac Safari — folder location

## Correct layout on your Mac

If the folder is on the Desktop as `riotemail`, it must look like this:

```
Desktop/
  riotemail/
    data/
      tasks.csv          ← accounts go HERE (not on Desktop itself)
    run_safari_mac.sh
    run_safari_batch.sh
    run_safari_batch.py
    safari_riot_email.applescript
    fetch_riot_imap_code.py
    RUN_ME.command
    MAC_SAFARI.md
```

Wrong:
- `Desktop/tasks.csv`
- Prefer `Desktop/riotemail/data/tasks.csv` (also accepts `Desktop/riotemail/tasks.csv`)

## Run

**Option A — double-click:** `RUN_ME.command`  
(Right-click → Open the first time if macOS blocks it.)

**Option B — Terminal:**

```bash
cd ~/Desktop/riotemail
# if you unzipped into a subfolder:
# cd ~/Desktop/riotemail/riot-email-update-safari-mac

chmod +x run_safari_mac.sh run_safari_batch.sh fetch_riot_imap_code.py RUN_ME.command
./run_safari_mac.sh
```

**Option C — open the AppleScript:**  
If you open `safari_riot_email.applescript` directly, it looks for `tasks.csv` and runs the batch using **this** toolkit’s scripts (not an older `~/Desktop/riotemail` copy).

Prefer Option A/B. The launcher prints `script folder:` and `using CSV:` so you can confirm the path.

## One-time Safari setup

1. Safari → Settings → Advanced → Show Develop menu  
2. Develop → Allow JavaScript from Apple Events  

## CSV format

```csv
riot_username,riot_password,imap_email,imap_app_password,new_email,imap_host,imap_port,proxy_index,email_change_url
RiotUser,YourRiotPass,you@icloud.com,xxxx-xxxx-xxxx-xxxx,new@icloud.com,imap.mail.me.com,993,,
```

Results: `success.txt` / `failed.txt` in the same folder as the scripts.

## Investigation logs

Each account run writes a timestamped file under `debug/logs/`:

```
debug/logs/safari_YYYYMMDD_HHMMSS_<username>.log
```

The log includes step markers (`STEP: fill_email`, …), IMAP notes, and on
failure a Safari URL/title + page probe (email field / SAVE button / captcha /
body snippet). Failures in `failed.txt` append the log path as a third field.

Share that `.log` file when asking for help with an error.

## IMAP verify mail (iCloud)

Riot’s “Verify Your Email” messages often arrive with an iCloud-rewritten
From address (`…riotgames_com…@icloud.com`). BUILD **2026-08-09k+** fetches
with `BODY.PEEK[]` and accepts those senders. Quick check on the Mac:

```bash
cd ~/Desktop/"riot-email-update-safari-mac 3"
IMAP_HOST=imap.mail.me.com IMAP_USER='you@icloud.com' IMAP_PASSWORD='app-password' \
  python3 fetch_riot_verify_link.py --timeout 30 --since-seconds 86400
```

If Script Editor reports an error about setting `line` / `st` / `key`, update to
the latest zip (**BUILD 2026-08-09h** or newer) — those names are reserved in
AppleScript.

## Account flow

For each CSV row, the runner:

1. Logs in and waits for `https://account.riotgames.com/`
2. Fills `personal-information-card__emailAddress`
3. Clicks `personal-information-card__saveChanges-btn` (**SAVE AND VERIFY**)
4. Waits via IMAP for a **Verify Your Email** message and opens its
   **Verify Email** link
5. Returns to the account page and clicks `log-out-everywhere-button`
6. Records the result, then starts the next CSV row

The zip contains only `data/tasks.csv.example`; updating the scripts will not
replace your existing `data/tasks.csv`.

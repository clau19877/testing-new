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

## Cloudflare / warm-up (BUILD 2026-08-12c+)

If Safari shows **Just a moment…** / Cloudflare:

1. Stop the batch. In Safari, open `https://www.apple.com` then `https://account.riotgames.com` and wait until pages load normally (solve any CF prompt once as yourself).
2. Re-run with a longer gap between accounts:
   ```bash
   SAFARI_BATCH_DELAY=40 ./run_safari_mac.sh
   ```
3. Prefer `./run_safari_mac.sh` / `RUN_ME.command` over hammering Script Editor.
4. The script warms Safari on apple.com, opens `account.riotgames.com`
   directly, uses jittered delays, and backs off when CF is detected.

Do **not** set `SAFARI_BATCH_DELAY` below ~15 when CF has already fired on your IP.

## CSV format

```csv
riot_username,riot_password,new_password,imap_email,imap_app_password,new_email,imap_host,imap_port,proxy_index,email_change_url,change_password
RiotUser,OldPass,NewPass,you@icloud.com,xxxx-xxxx-xxxx-xxxx,new@icloud.com,imap.mail.me.com,993,,,1
```

`change_password`: `1` = change password **after** email verify, `0` = skip.
Blank defaults to `1`. Whole-batch off: `SKIP_PASSWORD_CHANGE=1 ./run_safari_mac.sh`.

Results in the same folder as the scripts:
- `success.txt` — completed accounts (stores the **new** password when password change ran)
- `tasks.csv` — successful rows are **removed automatically** as they finish
- `failed.txt` — CSV with the same columns as `tasks.csv` (paste rows back to rerun).
  `riot_password` is set to `new_password` only if the password step had already
  run. Reasons/logs go to `failed_reasons.txt`.

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
with `BODY.PEEK[]` and accepts those senders. BUILD **2026-08-12c+** also
quotes IMAP mailbox names and skips missing folders — older builds could abort
the whole inbox poll with `SELECT … BAD Parse Error` while trying
`Junk Folder`. Quick check on the Mac:

```bash
cd ~/Desktop/"riot-email-update-safari-mac 3"
IMAP_HOST=imap.mail.me.com IMAP_USER='you@icloud.com' IMAP_PASSWORD='app-password' \
  python3 fetch_riot_verify_link.py --timeout 30 --since-seconds 86400
```

If Script Editor reports an error about setting `line` / `st` / `key`, update to
the latest zip (**BUILD 2026-08-12e** or newer) — those names are reserved in
AppleScript.

## Account flow

For each CSV row, the runner:

1. Clears any leftover Riot session (riotbar **Logout** if still logged in),
   then opens `https://account.riotgames.com/` (login form / redirect)
2. Logs in and waits for `https://account.riotgames.com/`
3. Fills `personal-information-card__emailAddress`
4. Clicks `personal-information-card__saveChanges-btn` (**SAVE AND VERIFY**)
5. Waits via IMAP for a **Verify Your Email** message and opens its
   **Verify Email** link
6. If `change_password=1`: changes password (`password-card__*` → submit),
   waits for success/error/session-drop, **force-logs out**, then re-logs in
   with `new_password` to confirm before any final logout
7. If password was changed: only after that re-login succeeds, proceeds to logout
8. Clicks riotbar **Logout** (`data-testid=riotbar:account:link-logout`),
   then verifies Safari is logged out before the next CSV row starts
9. Records the result, then starts the next CSV row

The zip contains only `data/tasks.csv.example`; updating the scripts will not
replace your existing `data/tasks.csv`.


## Run without interrupting your other work

By default (**BUILD 2026-08-12e+**) the script does **not** bring Safari to the
front. Page actions use AppleScript `do JavaScript` (not real mouse clicks), so
you can keep typing in other apps.

Tips:
- Move the Safari window to another Desktop / Mission Control Space
- Leave that Space alone while the batch runs
- Do not click inside the Safari window mid-run (can steal focus back)
- If you *want* Safari to jump to the front:  
  `SAFARI_STEAL_FOCUS=1 ./run_safari_mac.sh`

## Stop the batch immediately

- Terminal: press **Ctrl+C**
- Or double-click **STOP_BATCH.command**

**BUILD 2026-08-12c+** hard-kills the whole tree: `run_safari_batch.py`,
`osascript` / `safari_riot_email.applescript`, and IMAP helpers
(`fetch_riot_verify_link.py`). Remaining CSV rows do not start. The AppleScript
also watches `.safari_batch_stop` between wait ticks, wraps Safari Apple Events
in `with timeout`, and hard-caps IMAP `do shell script` with a perl alarm so a
hung helper cannot pin `osascript` forever. If anything still looks stuck, run
STOP again or quit the Terminal / Script Editor window.

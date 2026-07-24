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
- `Desktop/riotemail/tasks.csv` only (ok too if present, but prefer `data/tasks.csv`)
- Double-clicking `safari_riot_email.applescript`

## Run

**Option A — double-click:** `RUN_ME.command`  
(Right-click → Open the first time if macOS blocks it.)

**Option B — Terminal:**

```bash
cd ~/Desktop/riotemail
# if you unzipped into a subfolder:
# cd ~/Desktop/riotemail/riot-email-update-safari-mac

chmod +x *.sh *.command fetch_riot_imap_code.py
./run_safari_mac.sh
```

The launcher prints `script folder:` and `using CSV:` so you can confirm the path.

## One-time Safari setup

1. Safari → Settings → Advanced → Show Develop menu  
2. Develop → Allow JavaScript from Apple Events  

## CSV format

```csv
riot_username,riot_password,imap_email,imap_app_password,new_email,imap_host,imap_port,proxy_index,email_change_url
RiotUser,YourRiotPass,you@icloud.com,xxxx-xxxx-xxxx-xxxx,new@icloud.com,imap.mail.me.com,993,,
```

Results: `success.txt` / `failed.txt` in the same folder as the scripts.

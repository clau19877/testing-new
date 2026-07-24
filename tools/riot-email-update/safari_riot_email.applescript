(*
  Riot account email update via local macOS Safari.

  Why Safari: on a real Mac Safari session, Riot often skips hCaptcha.
  This script opens the docs.qq.com click-through, signs in, handles MFA
  (prompt or CLI arg), then updates the account email.

  Prerequisites
  -------------
  Safari → Settings → Advanced → show Develop menu
  Develop → Allow JavaScript from Apple Events  (must be ON)

  Run
  ---
    osascript safari_riot_email.applescript \
      --username 'RiotUser' \
      --password 'Secret' \
      --new-email 'new@icloud.com'

  Or with the helper (loads .env):
    ./run_safari_mac.sh

  Optional args:
    --entry-url URL     docs.qq.com interstitial (default built-in)
    --mfa-code CODE     skip MFA prompt if you already have the code
    --verify-code CODE  new-email verify code (skip prompt)
    --skip-email-change only login, do not change email
    --batch             no dialogs; fail if MFA/IMAP/captcha needs a human
*)

on run argv
	set opts to parseArgs(argv)

	set entryURL to optValue(opts, "entry-url", defaultEntryURL())
	set riotUser to optValue(opts, "username", "")
	set riotPass to optValue(opts, "password", "")
	set newEmail to optValue(opts, "new-email", "")
	set mfaCode to optValue(opts, "mfa-code", "")
	set verifyCode to optValue(opts, "verify-code", "")
	set skipEmail to optFlag(opts, "skip-email-change")
	set batchMode to optFlag(opts, "batch")

	if riotUser is "" then
		if batchMode then error "batch mode requires --username"
		set riotUser to text returned of (display dialog "Riot username or email:" default answer "")
	end if
	if riotPass is "" then
		if batchMode then error "batch mode requires --password"
		set riotPass to text returned of (display dialog "Riot password:" default answer "" with hidden answer)
	end if
	if (not skipEmail) and newEmail is "" then
		if batchMode then error "batch mode requires --new-email (or --skip-email-change)"
		set newEmail to text returned of (display dialog "New email address:" default answer "")
	end if

	logLine("Opening Safari → docs.qq.com entry…")
	tell application "Safari"
		activate
		try
			close every window
		end try
		make new document with properties {URL:entryURL}
	end tell
	delay 2.5

	-- Click Continue on the Tencent Docs interstitial
	logLine("Clicking Continue on interstitial…")
	safariJS("
		(function () {
		  const sels = [
		    'a[href*=\"account.riotgames.com\"]',
		    'a[href*=\"authenticate.riotgames.com\"]',
		    'a:has-text(\"Continue\")',
		    'button:has-text(\"Continue\")'
		  ];
		  // :has-text is Playwright-only — use text match in DOM
		  const candidates = Array.from(document.querySelectorAll('a,button'));
		  for (const el of candidates) {
		    const t = (el.textContent || '').trim();
		    const href = (el.getAttribute('href') || '');
		    if (/account\\.riotgames\\.com|authenticate\\.riotgames\\.com/i.test(href)
		        || /^Continue/i.test(t) || t.includes('继续')) {
		      el.click();
		      return 'clicked:' + (t || href).slice(0, 80);
		    }
		  }
		  // Fallback: follow ?url=
		  try {
		    const u = new URL(location.href);
		    const target = u.searchParams.get('url');
		    if (target) { location.href = target; return 'goto:' + target; }
		  } catch (e) {}
		  return 'no-continue';
		})();
	")
	delay 3.5
	waitForRiotLogin(45)

	logLine("Filling Riot login form…")
	set fillResult to safariJS("
		(function (user, pass) {
		  function setNative(el, value) {
		    if (!el) return false;
		    const proto = window.HTMLInputElement.prototype;
		    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
		    if (desc && desc.set) desc.set.call(el, value);
		    else el.value = value;
		    el.dispatchEvent(new Event('input', { bubbles: true }));
		    el.dispatchEvent(new Event('change', { bubbles: true }));
		    return true;
		  }
		  const userEl = document.querySelector('input[name=\"username\"], input[type=\"text\"], input[autocomplete=\"username\"]');
		  const passEl = document.querySelector('input[name=\"password\"], input[type=\"password\"]');
		  const okUser = setNative(userEl, user);
		  const okPass = setNative(passEl, pass);
		  // dismiss cookie banner if present
		  const cookie = document.querySelector('button.osano-cm-dialog__close, button[aria-label*=\"Close\"]');
		  if (cookie) try { cookie.click(); } catch (e) {}
		  return JSON.stringify({ user: okUser, pass: okPass, href: location.href });
		})(" & jsonString(riotUser) & ", " & jsonString(riotPass) & ");
	")
	logLine("Fill result: " & fillResult)
	delay 0.6

	logLine("Clicking Sign in…")
	safariJS("
		(function () {
		  const btn = document.querySelector('button[data-testid=\"btn-signin-submit\"], button[type=\"submit\"]');
		  if (btn) { btn.click(); return 'clicked-signin'; }
		  const form = document.querySelector('form');
		  if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return 'submitted-form'; }
		  return 'no-signin';
		})();
	")

	-- Wait for MFA, captcha, account, or error
	set phase to waitForPostLogin(90)
	logLine("Post-login phase: " & phase)

	if phase is "captcha" then
		if batchMode then error "hCaptcha appeared (batch mode will not wait for manual solve)"
		display dialog "hCaptcha appeared in Safari. Solve it in the Safari window, then click OK." buttons {"OK"} default button 1
		set phase to waitForPostLogin(120)
		logLine("After manual captcha phase: " & phase)
	end if

	if phase is "bad_creds" then
		error "Riot rejected username/password (Check your details and try again)."
	end if

	if phase is "mfa" then
		if mfaCode is "" then
			set mfaCode to fetchImapCode("IMAP")
		end if
		if mfaCode is "" then
			if batchMode then error "MFA required but no IMAP code (set IMAP_* / imap columns)"
			set mfaCode to text returned of (display dialog "Enter Riot MFA / email code:" default answer "")
		end if
		logLine("Submitting MFA code…")
		safariJS("
			(function (code) {
			  function setNative(el, value) {
			    if (!el) return false;
			    const proto = window.HTMLInputElement.prototype;
			    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
			    if (desc && desc.set) desc.set.call(el, value);
			    else el.value = value;
			    el.dispatchEvent(new Event('input', { bubbles: true }));
			    el.dispatchEvent(new Event('change', { bubbles: true }));
			    return true;
			  }
			  const el = document.querySelector('input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[type=\"tel\"], input[type=\"text\"]');
			  setNative(el, code);
			  const btn = document.querySelector('button[type=\"submit\"], button[data-testid*=\"submit\"], button');
			  if (btn) btn.click();
			  return el ? 'mfa-filled' : 'mfa-input-missing';
			})(" & jsonString(mfaCode) & ");
		")
		delay 3
		set phase to waitForPostLogin(60)
		logLine("After MFA phase: " & phase)
	end if

	if phase is not "logged_in" and phase is not "account" then
		-- One more chance: maybe already on account
		tell application "Safari" to set cur to URL of document 1
		if cur does not contain "account.riotgames.com" then
			error "Login did not reach account.riotgames.com (phase=" & phase & "). Check Safari window."
		end if
	end if

	if skipEmail then
		logLine("Skipping email change (--skip-email-change).")
		if not batchMode then display dialog "Logged in via Safari. Email change skipped." buttons {"OK"} default button 1
		return "login_ok"
	end if

	logLine("Opening account email settings…")
	tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
	delay 3

	-- Try to open email edit UI
	safariJS("
		(function () {
		  const links = Array.from(document.querySelectorAll('a,button,[role=\"button\"]'));
		  for (const el of links) {
		    const t = (el.textContent || '').toLowerCase();
		    if (t.includes('email') || t.includes('change email') || t.includes('edit')) {
		      el.click();
		      return 'clicked:' + t.slice(0, 60);
		    }
		  }
		  return 'no-email-control';
		})();
	")
	delay 2

	logLine("Filling new email: " & newEmail)
	set emailFill to safariJS("
		(function (email) {
		  function setNative(el, value) {
		    if (!el) return false;
		    const proto = window.HTMLInputElement.prototype;
		    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
		    if (desc && desc.set) desc.set.call(el, value);
		    else el.value = value;
		    el.dispatchEvent(new Event('input', { bubbles: true }));
		    el.dispatchEvent(new Event('change', { bubbles: true }));
		    return true;
		  }
		  const inputs = Array.from(document.querySelectorAll('input[type=\"email\"], input[name*=\"email\" i], input[id*=\"email\" i]'));
		  let n = 0;
		  for (const el of inputs) {
		    if (setNative(el, email)) n++;
		  }
		  return 'filled=' + n;
		})(" & jsonString(newEmail) & ");
	")
	logLine(emailFill)

	-- Submit email change if a save/submit button is visible
	safariJS("
		(function () {
		  const buttons = Array.from(document.querySelectorAll('button, input[type=\"submit\"]'));
		  for (const el of buttons) {
		    const t = ((el.textContent || el.value || '') + '').toLowerCase();
		    if (/save|submit|continue|confirm|send|update|change/.test(t)) {
		      el.click();
		      return 'clicked:' + t.slice(0, 40);
		    }
		  }
		  return 'no-submit';
		})();
	")
	delay 2.5

	if not batchMode then
		display dialog "Review the Safari window: confirm the new email looks right, then click OK to continue (verify code next if needed)." buttons {"OK"} default button 1
	end if

	if verifyCode is "" then
		set verifyCode to fetchImapCode("NEW_IMAP")
		if verifyCode is "" then set verifyCode to fetchImapCode("IMAP")
	end if
	if verifyCode is "" and not batchMode then
		try
			set verifyCode to text returned of (display dialog "New-email verify code (leave blank to finish manually in Safari):" default answer "")
		on error
			set verifyCode to ""
		end try
	end if

	if verifyCode is not "" then
		safariJS("
			(function (code) {
			  function setNative(el, value) {
			    if (!el) return false;
			    const proto = window.HTMLInputElement.prototype;
			    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
			    if (desc && desc.set) desc.set.call(el, value);
			    else el.value = value;
			    el.dispatchEvent(new Event('input', { bubbles: true }));
			    el.dispatchEvent(new Event('change', { bubbles: true }));
			    return true;
			  }
			  const el = document.querySelector('input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[type=\"tel\"], input[type=\"text\"]');
			  setNative(el, code);
			  const btn = document.querySelector('button[type=\"submit\"], button[data-testid*=\"submit\"]');
			  if (btn) btn.click();
			  return el ? 'verify-filled' : 'verify-missing';
			})(" & jsonString(verifyCode) & ");
		")
		delay 2
	else if batchMode then
		logLine("No verify code from IMAP — assuming save without code / check manually later")
	end if

	if not batchMode then
		display dialog "Safari flow finished. Confirm the email change completed in the Safari window." buttons {"OK"} default button 1
	end if
	logLine("SUCCESS")
	return "done"
end run


-- ============== helpers ==============

on defaultEntryURL()
	return "https://docs.qq.com/scenario/link.html?url=https%3A%2F%2Faccount.riotgames.com%2F&pid=300000000%24KrVGtggzglZK&cid=144115210422737002&nlc=1"
end defaultEntryURL

on logLine(msg)
	log msg
	try
		do shell script "printf '%s\\n' " & quoted form of ("[safari-riot] " & msg) & " >&2"
	end try
end logLine

on jsonString(s)
	-- Produce a JS string literal
	set s to s as text
	set s to replaceText(s, "\\", "\\\\")
	set s to replaceText(s, "\"", "\\\"")
	set s to replaceText(s, return, "\\n")
	set s to replaceText(s, linefeed, "\\n")
	return "\"" & s & "\""
end jsonString

on replaceText(theText, oldString, newString)
	set AppleScript's text item delimiters to oldString
	set theItems to text items of theText
	set AppleScript's text item delimiters to newString
	set theText to theItems as text
	set AppleScript's text item delimiters to ""
	return theText
end replaceText

on safariJS(js)
	tell application "Safari"
		try
			return do JavaScript js in document 1
		on error errMsg number errNum
			if errNum is -1728 or errMsg contains "JavaScript from Apple Events" or errMsg contains "Allow JavaScript" then
				error "Enable Safari → Develop → Allow JavaScript from Apple Events, then re-run. (" & errMsg & ")"
			end if
			error errMsg
		end try
	end tell
end safariJS

on waitForRiotLogin(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		tell application "Safari" to set cur to URL of document 1
		if cur contains "riotgames.com" then
			delay 1
			return
		end if
		delay 0.5
	end repeat
	error "Timed out waiting for Riot login after docs.qq.com Continue. URL still not riotgames.com."
end waitForRiotLogin

on waitForPostLogin(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set st to safariJS("
			(function () {
			  const href = location.href || '';
			  const body = (document.body && document.body.innerText || '').toLowerCase();
			  if (/incorrect|check your details|try again/.test(body) && /password|username|sign in/.test(body))
			    return 'bad_creds';
			  if (document.querySelector('iframe[src*=\"hcaptcha.com\"]'))
			    return 'captcha';
			  if (/code|verification|authenticate|two-factor|2fa|email you/.test(body)
			      && document.querySelector('input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"]'))
			    return 'mfa';
			  if (/account\\.riotgames\\.com/.test(href) && !/log-in|login|oauth2\\/log-in/.test(href))
			    return 'account';
			  if (/account\\.riotgames\\.com/.test(href))
			    return 'logged_in';
			  if (document.querySelector('input[name=\"username\"], input[type=\"password\"]'))
			    return 'login';
			  return 'unknown';
			})();
		")
		if st is in {"bad_creds", "captcha", "mfa", "account", "logged_in"} then return st
		delay 0.8
	end repeat
	return "timeout"
end waitForPostLogin

on parseArgs(argv)
	set opts to {}
	set i to 1
	repeat while i ≤ (count of argv)
		set a to item i of argv as text
		if a starts with "--" then
			set key to text 3 thru -1 of a
			if key is "skip-email-change" or key is "batch" then
				set end of opts to {key, "1"}
			else if i < (count of argv) then
				set i to i + 1
				set end of opts to {key, item i of argv as text}
			else
				set end of opts to {key, "1"}
			end if
		end if
		set i to i + 1
	end repeat
	return opts
end parseArgs

on optValue(opts, key, defaultValue)
	repeat with p in opts
		if item 1 of p is key then return item 2 of p
	end repeat
	return defaultValue
end optValue

on optFlag(opts, key)
	return optValue(opts, key, "") is "1"
end optFlag

on scriptDir()
	try
		set p to POSIX path of (path to me)
		-- When run as .applescript text via osascript, path to me may be osascript itself.
		-- Prefer TOOL_DIR env from the launcher.
		set envDir to do shell script "printf '%s' \"${TOOL_DIR:-}\""
		if envDir is not "" then return envDir
		return do shell script "dirname " & quoted form of p
	on error
		return do shell script "pwd"
	end try
end scriptDir

on fetchImapCode(prefix)
	-- Uses fetch_riot_imap_code.py + .env IMAP_* / NEW_IMAP_* when available.
	try
		set dir to scriptDir()
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
		set cmd to "cd " & quoted form of dir & " && TOOL_DIR=" & quoted form of dir & " " & quoted form of py & " " & quoted form of (dir & "/fetch_riot_imap_code.py") & " --prefix " & quoted form of prefix & " --timeout 90 --since-seconds 240"
		logLine("IMAP fetch (" & prefix & ")…")
		set code to do shell script cmd
		if code is not "" then
			logLine("IMAP code received (" & prefix & ")")
			return code
		end if
	on error errMsg
		logLine("IMAP fetch skipped/failed: " & errMsg)
	end try
	return ""
end fetchImapCode

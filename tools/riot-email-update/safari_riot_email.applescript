-- Riot account email update via local macOS Safari.
--
-- Why Safari: on a real Mac Safari session, Riot often skips hCaptcha.
-- Opens docs.qq.com click-through, signs in, handles MFA, updates email.
--
-- Prerequisites:
--   Safari -> Settings -> Advanced -> show Develop menu
--   Develop -> Allow JavaScript from Apple Events (must be ON)
--
-- Run:
--   osascript safari_riot_email.applescript -username RiotUser -password Secret -new-email new@icloud.com
--   ./run_safari_mac.sh
--
-- Optional args: entry-url, mfa-code, verify-code, skip-email-change, batch
--
-- Note: do not embed JS with backslash-quote inside AppleScript string literals.
-- Use the js* handlers below (single-quoted JS, AppleScript quote for dynamics).

-- Per-run debug log path (set by logInit). Empty means file logging is off.
property logFilePath : ""
property logAccountLabel : ""


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

	-- Primary source for batch: task file written from tasks.csv (reliable).
	set taskFile to optValue(opts, "task-file", "")
	if taskFile is "" then set taskFile to envOrEmpty("SAFARI_TASK_FILE")
	if taskFile is not "" then
		set batchMode to true
		set tfUser to taskFileValue(taskFile, "riot_username")
		set tfPass to taskFileValue(taskFile, "riot_password")
		set tfEmail to taskFileValue(taskFile, "new_email")
		set tfEntry to taskFileValue(taskFile, "entry_url")
		if tfUser is not "" then set riotUser to tfUser
		if tfPass is not "" then set riotPass to tfPass
		if tfEmail is not "" then set newEmail to tfEmail
		if tfEntry is not "" then set entryURL to tfEntry
		if taskFileValue(taskFile, "skip_email_change") is "1" then set skipEmail to true
	end if

	-- Fall back to env (batch runner also sets these from tasks.csv).
	if riotUser is "" then set riotUser to envOrEmpty("RIOT_USERNAME")
	if riotPass is "" then set riotPass to envOrEmpty("RIOT_PASSWORD")
	if newEmail is "" then set newEmail to envOrEmpty("NEW_EMAIL")
	if entryURL is defaultEntryURL() then
		set envEntry to envOrEmpty("LOGIN_ENTRY_URL")
		if envEntry is "" then set envEntry to envOrEmpty("LOGIN_URL")
		if envEntry is not "" then set entryURL to envEntry
	end if
	if (not batchMode) and (envOrEmpty("SAFARI_BATCH") is "1") then
		set batchMode to true
	end if

	-- Start a per-account debug log now that the username is resolved.
	logInit(riotUser)
	if taskFile is not "" then logLine("Loaded task file: " & taskFile)

	-- Opened directly (Script Editor / double-click) with no account supplied:
	-- search Desktop for data/tasks.csv (handles "riot-email-update-safari-mac 3").
	if riotUser is "" and taskFile is "" and (not batchMode) then
		set didBatch to runBatchFromCsv()
		if didBatch then return "batch_done"
	end if

	if riotUser is "" then
		if batchMode then error "batch mode: no riot_username (check tasks.csv / task file)"
		set hint to discoverHint()
		error "No account found." & return & return & hint
	end if
	if riotPass is "" then
		if batchMode then error "batch mode: no riot_password (check tasks.csv)"
		error "No Riot password in the task. Check data/tasks.csv riot_password column."
	end if
	if (not skipEmail) and newEmail is "" then
		if batchMode then error "batch mode: no new_email (check tasks.csv)"
		error "No new email in the task. Check data/tasks.csv new_email column."
	end if

	logLine("Account: " & riotUser & " -> " & newEmail)
	try
		logStep("open_entry")
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
		logStep("click_continue")
		logLine("Clicking Continue on interstitial…")
		safariJS(jsClickContinue())
		delay 3.5
		waitForRiotLogin(45)

		logStep("fill_login")
		logLine("Filling Riot login form…")
		set fillResult to safariJS(jsFillLogin(riotUser, riotPass))
		logLine("Fill result: " & fillResult)
		delay 0.6

		logStep("click_sign_in")
		logLine("Clicking Sign in…")
		safariJS(jsClickSignIn())

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
			logStep("mfa")
			if mfaCode is "" then
				set mfaCode to fetchImapCode("IMAP")
			end if
			if mfaCode is "" then
				if batchMode then error "MFA required but no IMAP code (set IMAP_* / imap columns)"
				set mfaCode to text returned of (display dialog "Enter Riot MFA / email code:" default answer "")
			end if
			logLine("Submitting MFA code…")
			safariJS(jsSubmitCode(mfaCode, "mfa"))
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
			logLine("SUCCESS")
			logLine("Debug log: " & logFilePath)
			return "login_ok"
		end if

		logStep("open_account")
		logLine("Opening account email settings…")
		tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
		delay 2

		-- Wait for Riot personal-information email field
		logStep("wait_email_field")
		logLine("Waiting for personal-information-card__emailAddress…")
		set emailReady to waitForEmailField(45)
		logLine("Email field ready: " & emailReady)
		if emailReady is not "ready" then
			-- Try clicking edit/email controls once, then wait again
			safariJS(jsClickEmailControl())
			delay 2
			set emailReady to waitForEmailField(30)
			logLine("Email field ready (retry): " & emailReady)
		end if
		if emailReady is not "ready" then error "Could not find personal-information-card__emailAddress on account page."

		-- Click the email field (focus / enable edit), then type the new address
		logStep("fill_email")
		logLine("Clicking email field…")
		logLine(safariJS(jsClickEmailField()))
		delay 0.4

		logLine("Filling new email: " & newEmail)
		set emailFill to safariJS(jsFillEmail(newEmail))
		logLine(emailFill)
		if emailFill does not contain "filled=1" and emailFill does not contain "ok" then
			error "Failed to type new email into personal-information-card__emailAddress (" & emailFill & ")"
		end if
		delay 0.6

		-- Click SAVE AND VERIFY (personal-information-card__saveChanges-btn)
		set verifySinceEpoch to do shell script "date +%s"
		logStep("save_and_verify")
		logLine("Waiting for SAVE AND VERIFY button…")
		set saveReady to waitForSaveButton(20)
		logLine("Save button ready: " & saveReady)
		if saveReady is not "ready" then error "SAVE AND VERIFY button did not become enabled (personal-information-card__saveChanges-btn)."
		logLine("Clicking SAVE AND VERIFY…")
		set submitResult to safariJS(jsClickSaveAndVerify())
		logLine(submitResult)
		if submitResult does not contain "clicked-save" then error "Could not click SAVE AND VERIFY (" & submitResult & ")."
		delay 3

		-- If an hCaptcha challenge becomes visible, allow solving (skip in batch)
		if safariJS(jsCaptchaVisible()) is "captcha" then
			if batchMode then
				logLine("hCaptcha challenge visible during save — waiting up to 60s for auto/solve…")
				set waited to 0
				repeat while waited < 60 and (safariJS(jsCaptchaVisible()) is "captcha")
					delay 3
					set waited to waited + 3
				end repeat
			else
				display dialog "Solve the hCaptcha in Safari, then click OK." buttons {"OK"} default button 1
			end if
		end if
		delay 2

		-- Email verification: fetch the "Verify Your Email" link via IMAP and open it.
		logStep("imap_verify_link")
		logLine("Fetching verification link via IMAP…")
		set verifyLink to fetchImapVerifyLink("NEW_IMAP", verifySinceEpoch)
		if verifyLink is "" then set verifyLink to fetchImapVerifyLink("IMAP", verifySinceEpoch)

		if verifyLink is not "" then
			logLine("Opening verification link in Safari…")
			tell application "Safari" to set URL of document 1 to verifyLink
			delay 4
			-- Some landing pages need a confirm/verify click.
			logLine(safariJS(jsClickVerifyOnLanding()))
			delay 2
		else
			if batchMode then
				error "No Verify Your Email link found via IMAP within timeout."
			else
				display dialog "No verification link found via IMAP. Verify the email manually in Safari, then click OK." buttons {"OK"} default button 1
			end if
		end if

		-- Log out everywhere, then move on to the next account.
		logStep("logout_everywhere")
		logLine("Returning to account page to log out everywhere…")
		tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
		delay 3
		set logoutReady to waitForLogoutButton(20)
		logLine("Logout button ready: " & logoutReady)
		if logoutReady is not "ready" then error "Could not find log-out-everywhere-button after verification."
		logLine("Clicking LOG OUT EVERYWHERE…")
		set logoutResult to safariJS(jsClickLogoutEverywhere())
		logLine(logoutResult)
		if logoutResult does not contain "clicked-logout" then error "Could not click LOG OUT EVERYWHERE (" & logoutResult & ")."
		delay 1
		logLine(safariJS(jsConfirmLogoutEverywhere()))
		delay 2

		if not batchMode then
			display dialog "Finished this account (email changed + verify attempted + logged out)." buttons {"OK"} default button 1
		end if
		logLine("SUCCESS")
		logLine("Debug log: " & logFilePath)
		return "done"
	on error errMsg number errNum
		logLine("STEP FAILED: " & errMsg & " (" & errNum & ")")
		dumpDebug("failure")
		logLine("Debug log: " & logFilePath)
		error errMsg number errNum
	end try
end run


-- ============== helpers ==============

on defaultEntryURL()
	return "https://docs.qq.com/scenario/link.html?url=https%3A%2F%2Faccount.riotgames.com%2F&pid=300000000%24KrVGtggzglZK&cid=144115210422737002&nlc=1"
end defaultEntryURL

on backslashChar()
	-- Return ASCII backslash without putting a backslash in source text.
	return character id 92
end backslashChar

on logSafeLabel(labelText)
	-- Keep filename-safe account tags short.
	try
		set raw to labelText as text
		if raw is "" then set raw to "run"
		set cmd to "printf '%s' " & quoted form of raw & " | tr -c 'A-Za-z0-9._-' '_' | cut -c1-48"
		set safe to do shell script cmd
		if safe is "" then return "run"
		return safe
	on error
		return "run"
	end try
end logSafeLabel

on logInit(accountLabel)
	-- Create (or reuse) debug/logs/safari_YYYYMMDD_HHMMSS_<user>.log for error investigation.
	set logAccountLabel to accountLabel as text
	set existing to envOrEmpty("SAFARI_LOG_PATH")
	if existing is not "" then
		set logFilePath to existing
	else
		set dir to scriptDir() & "/debug/logs"
		try
			do shell script "mkdir -p " & quoted form of dir
		end try
		set stamp to do shell script "date +%Y%m%d_%H%M%S"
		set safe to logSafeLabel(logAccountLabel)
		set logFilePath to dir & "/safari_" & stamp & "_" & safe & ".log"
	end if
	logLine("=== safari-riot session start ===")
	logLine("BUILD 2026-08-09h")
	logLine("log file → " & logFilePath)
	if logAccountLabel is not "" then logLine("account=" & logAccountLabel)
	try
		logLine("tool dir=" & scriptDir())
	end try
	try
		logLine("cwd=" & (do shell script "pwd"))
	end try
end logInit

on logLine(msg)
	-- Console + durable investigation file (timestamped).
	-- Avoid reserved AppleScript names: line, body, text, key, etc.
	log msg
	set msgText to msg as text
	set ts to ""
	try
		set ts to do shell script "date '+%Y-%m-%d %H:%M:%S'"
	end try
	if ts is "" then
		set logEntry to "[safari-riot] " & msgText
	else
		set logEntry to "[" & ts & "] [safari-riot] " & msgText
	end if
	try
		do shell script "echo " & quoted form of logEntry & " >&2"
	end try
	if logFilePath is not "" then
		try
			do shell script "printf '%s\n' " & quoted form of logEntry & " >> " & quoted form of logFilePath
		end try
	end if
end logLine

on logStep(stepName)
	logLine("STEP: " & stepName)
end logStep

on dumpDebug(reasonLabel)
	-- Capture Safari URL/title + DOM probes for post-mortem investigation.
	logLine("--- debug dump (" & reasonLabel & ") ---")
	try
		tell application "Safari"
			try
				set curURL to URL of document 1
				logLine("safari.url=" & curURL)
			on error errMsg
				logLine("safari.url=<unavailable> (" & errMsg & ")")
			end try
			try
				set curName to name of document 1
				logLine("safari.title=" & curName)
			on error errMsg
				logLine("safari.title=<unavailable> (" & errMsg & ")")
			end try
		end tell
	on error errMsg
		logLine("safari dump failed: " & errMsg)
	end try
	try
		set probe to safariJS(jsDumpPageState())
		logLine("page.probe=" & probe)
	on error errMsg
		logLine("page.probe=<failed> (" & errMsg & ")")
	end try
	logLine("--- end debug dump ---")
end dumpDebug

on jsonString(s)
	-- Produce a JS string literal safely for embedding in do JavaScript.
	set bs to backslashChar()
	set s to s as text
	set s to replaceText(s, bs, bs & bs)
	set s to replaceText(s, quote, bs & quote)
	set s to replaceText(s, return, bs & "n")
	set s to replaceText(s, linefeed, bs & "n")
	return quote & s & quote
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

-- JS builders: keep double-quotes out of AppleScript string literals.
-- Use only single quotes in the JS source text below.

on jsClickContinue()
	set bs to backslashChar()
	return "(function () {" & ¬
		"  const candidates = Array.from(document.querySelectorAll('a,button'));" & ¬
		"  for (const el of candidates) {" & ¬
		"    const t = (el.textContent || '').trim();" & ¬
		"    const href = (el.getAttribute('href') || '');" & ¬
		"    if (/account" & bs & ".riotgames" & bs & ".com|authenticate" & bs & ".riotgames" & bs & ".com/i.test(href)" & ¬
		"        || /^Continue/i.test(t) || t.indexOf('Continue') === 0) {" & ¬
		"      el.click();" & ¬
		"      return 'clicked:' + (t || href).slice(0, 80);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  try {" & ¬
		"    const u = new URL(location.href);" & ¬
		"    const target = u.searchParams.get('url');" & ¬
		"    if (target) { location.href = target; return 'goto:' + target; }" & ¬
		"  } catch (e) {}" & ¬
		"  return 'no-continue';" & ¬
		"})();"
end jsClickContinue

on jsFillLogin(userName, passText)
	return "(function (user, pass) {" & ¬
		"  function setNative(el, value) {" & ¬
		"    if (!el) return false;" & ¬
		"    const proto = window.HTMLInputElement.prototype;" & ¬
		"    const desc = Object.getOwnPropertyDescriptor(proto, 'value');" & ¬
		"    if (desc && desc.set) desc.set.call(el, value);" & ¬
		"    else el.value = value;" & ¬
		"    el.dispatchEvent(new Event('input', { bubbles: true }));" & ¬
		"    el.dispatchEvent(new Event('change', { bubbles: true }));" & ¬
		"    return true;" & ¬
		"  }" & ¬
		"  const userEl = document.querySelector('input[name=username], input[type=text], input[autocomplete=username]');" & ¬
		"  const passEl = document.querySelector('input[name=password], input[type=password]');" & ¬
		"  const okUser = setNative(userEl, user);" & ¬
		"  const okPass = setNative(passEl, pass);" & ¬
		"  const cookie = document.querySelector('button.osano-cm-dialog__close, button[aria-label*=Close]');" & ¬
		"  if (cookie) try { cookie.click(); } catch (e) {}" & ¬
		"  return JSON.stringify({ user: okUser, pass: okPass, href: location.href });" & ¬
		"})(" & jsonString(userName) & ", " & jsonString(passText) & ");"
end jsFillLogin

on jsClickSignIn()
	return "(function () {" & ¬
		"  const btn = document.querySelector('button[data-testid=btn-signin-submit], button[type=submit]');" & ¬
		"  if (btn) { btn.click(); return 'clicked-signin'; }" & ¬
		"  const form = document.querySelector('form');" & ¬
		"  if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return 'submitted-form'; }" & ¬
		"  return 'no-signin';" & ¬
		"})();"
end jsClickSignIn

on jsProbePhase()
	set bs to backslashChar()
	return "(function () {" & ¬
		"  const href = location.href || '';" & ¬
		"  const body = (document.body && document.body.innerText || '').toLowerCase();" & ¬
		"  if (/incorrect|check your details|try again/.test(body) && /password|username|sign in/.test(body))" & ¬
		"    return 'bad_creds';" & ¬
		"  if (document.querySelector('iframe[src*=hcaptcha.com]'))" & ¬
		"    return 'captcha';" & ¬
		"  if (/code|verification|authenticate|two-factor|2fa|email you/.test(body)" & ¬
		"      && document.querySelector('input[name=code], input[autocomplete=one-time-code], input[inputmode=numeric]'))" & ¬
		"    return 'mfa';" & ¬
		"  if (/account" & bs & ".riotgames" & bs & ".com/.test(href) && href.indexOf('log-in') < 0 && href.indexOf('login') < 0 && href.indexOf('oauth2') < 0)" & ¬
		"    return 'account';" & ¬
		"  if (/account" & bs & ".riotgames" & bs & ".com/.test(href))" & ¬
		"    return 'logged_in';" & ¬
		"  if (document.querySelector('input[name=username], input[type=password]'))" & ¬
		"    return 'login';" & ¬
		"  return 'unknown';" & ¬
		"})();"
end jsProbePhase

on jsSubmitCode(codeText, kindText)
	return "(function (code) {" & ¬
		"  function setNative(el, value) {" & ¬
		"    if (!el) return false;" & ¬
		"    const proto = window.HTMLInputElement.prototype;" & ¬
		"    const desc = Object.getOwnPropertyDescriptor(proto, 'value');" & ¬
		"    if (desc && desc.set) desc.set.call(el, value);" & ¬
		"    else el.value = value;" & ¬
		"    el.dispatchEvent(new Event('input', { bubbles: true }));" & ¬
		"    el.dispatchEvent(new Event('change', { bubbles: true }));" & ¬
		"    return true;" & ¬
		"  }" & ¬
		"  const el = document.querySelector('input[name=code], input[autocomplete=one-time-code], input[inputmode=numeric], input[type=tel], input[type=text]');" & ¬
		"  setNative(el, code);" & ¬
		"  const btn = document.querySelector('button[type=submit], button[data-testid*=submit], button');" & ¬
		"  if (btn) btn.click();" & ¬
		"  return el ? '" & kindText & "-filled' : '" & kindText & "-input-missing';" & ¬
		"})(" & jsonString(codeText) & ");"
end jsSubmitCode

on emailFieldSelector()
	-- Riot account personal info email input (type=text, not type=email).
	return "input[data-testid=personal-information-card__emailAddress]"
end emailFieldSelector

on jsClickEmailControl()
	return "(function () {" & ¬
		"  const sel = 'input[data-testid=personal-information-card__emailAddress]';" & ¬
		"  const field = document.querySelector(sel);" & ¬
		"  if (field) { field.focus(); field.click(); return 'clicked-email-field'; }" & ¬
		"  const links = Array.from(document.querySelectorAll('a,button,[role=button], [data-testid*=email], [data-testid*=personal-information]'));" & ¬
		"  for (const el of links) {" & ¬
		"    const t = ((el.textContent || '') + ' ' + (el.getAttribute('data-testid') || '')).toLowerCase();" & ¬
		"    if (t.includes('email') || t.includes('change email') || t.includes('edit') || t.includes('personal-information')) {" & ¬
		"      el.click();" & ¬
		"      return 'clicked:' + t.slice(0, 60);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  return 'no-email-control';" & ¬
		"})();"
end jsClickEmailControl

on jsClickEmailField()
	return "(function () {" & ¬
		"  const el = document.querySelector('input[data-testid=personal-information-card__emailAddress]');" & ¬
		"  if (!el) return 'missing-email-field';" & ¬
		"  el.scrollIntoView({block:'center'});" & ¬
		"  el.focus();" & ¬
		"  el.click();" & ¬
		"  return 'clicked-email-field value=' + String(el.value || '').slice(0, 40);" & ¬
		"})();"
end jsClickEmailField

on jsFillEmail(emailText)
	return "(function (email) {" & ¬
		"  function setNative(el, value) {" & ¬
		"    if (!el) return false;" & ¬
		"    el.focus();" & ¬
		"    el.click();" & ¬
		"    const proto = window.HTMLInputElement.prototype;" & ¬
		"    const desc = Object.getOwnPropertyDescriptor(proto, 'value');" & ¬
		"    if (desc && desc.set) desc.set.call(el, '');" & ¬
		"    else el.value = '';" & ¬
		"    el.dispatchEvent(new Event('input', { bubbles: true }));" & ¬
		"    if (desc && desc.set) desc.set.call(el, value);" & ¬
		"    else el.value = value;" & ¬
		"    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));" & ¬
		"    el.dispatchEvent(new Event('change', { bubbles: true }));" & ¬
		"    el.dispatchEvent(new Event('blur', { bubbles: true }));" & ¬
		"    return el.value === value;" & ¬
		"  }" & ¬
		"  const sel = 'input[data-testid=personal-information-card__emailAddress]';" & ¬
		"  let el = document.querySelector(sel);" & ¬
		"  if (!el) {" & ¬
		"    const inputs = Array.from(document.querySelectorAll('input[type=email], input[type=text], input[name*=email i], input[id*=email i]'));" & ¬
		"    el = inputs.find(function (x) {" & ¬
		"      const id = ((x.getAttribute('data-testid') || '') + ' ' + (x.name || '') + ' ' + (x.id || '')).toLowerCase();" & ¬
		"      return id.includes('email');" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!el) return 'filled=0 missing-field';" & ¬
		"  const ok = setNative(el, email);" & ¬
		"  return (ok ? 'filled=1 ok' : 'filled=0 mismatch') + ' testid=' + (el.getAttribute('data-testid') || '') + ' value=' + String(el.value || '').slice(0, 60);" & ¬
		"})(" & jsonString(emailText) & ");"
end jsFillEmail

on jsClickSaveAndVerify()
	-- Riot save button appears once the email field is dirty:
	-- button data-testid=personal-information-card__saveChanges-btn title=SAVE AND VERIFY
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=personal-information-card__saveChanges-btn]');" & ¬
		"  if (!btn) {" & ¬
		"    var cands = Array.from(document.querySelectorAll('#personal-information button, .personal-information-card__buttonSection button'));" & ¬
		"    btn = cands.find(function (b) {" & ¬
		"      var t = ((b.textContent || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();" & ¬
		"      return t.indexOf('save') >= 0;" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!btn) return 'no-save-button';" & ¬
		"  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'save-button-disabled';" & ¬
		"  btn.scrollIntoView({ block: 'center' });" & ¬
		"  btn.click();" & ¬
		"  return 'clicked-save:' + ((btn.getAttribute('title') || btn.textContent || '').trim().slice(0, 40));" & ¬
		"})();"
end jsClickSaveAndVerify

on jsCaptchaVisible()
	return "(function () {" & ¬
		"  var frames = Array.from(document.querySelectorAll('iframe[src*=hcaptcha.com]'));" & ¬
		"  for (var i = 0; i < frames.length; i++) {" & ¬
		"    var f = frames[i];" & ¬
		"    var r = f.getBoundingClientRect();" & ¬
		"    var vis = r.width > 50 && r.height > 50 && f.offsetParent !== null;" & ¬
		"    if (vis && (f.src.indexOf('frame=challenge') >= 0 || f.src.indexOf('checkbox') >= 0)) return 'captcha';" & ¬
		"  }" & ¬
		"  return 'none';" & ¬
		"})();"
end jsCaptchaVisible

on jsClickVerifyOnLanding()
	return "(function () {" & ¬
		"  var btns = Array.from(document.querySelectorAll('button, a[role=button], input[type=submit], a'));" & ¬
		"  for (var i = 0; i < btns.length; i++) {" & ¬
		"    var el = btns[i];" & ¬
		"    var t = ((el.textContent || el.value || '') + ' ' + (el.getAttribute('data-testid') || '')).toLowerCase();" & ¬
		"    if (/verify email|verify your email|verify|confirm email|confirm/.test(t)) {" & ¬
		"      el.click();" & ¬
		"      return 'landing-clicked:' + t.trim().slice(0, 40);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  return 'landing-no-button';" & ¬
		"})();"
end jsClickVerifyOnLanding

on jsClickLogoutEverywhere()
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=log-out-everywhere-button]');" & ¬
		"  if (!btn) {" & ¬
		"    var cands = Array.from(document.querySelectorAll('button'));" & ¬
		"    btn = cands.find(function (b) {" & ¬
		"      var t = ((b.textContent || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();" & ¬
		"      return t.indexOf('log out everywhere') >= 0;" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!btn) return 'no-logout-button';" & ¬
		"  btn.scrollIntoView({ block: 'center' });" & ¬
		"  btn.click();" & ¬
		"  return 'clicked-logout';" & ¬
		"})();"
end jsClickLogoutEverywhere

on jsConfirmLogoutEverywhere()
	-- Riot may show a confirmation dialog after LOG OUT EVERYWHERE.
	return "(function () {" & ¬
		"  var roots = Array.from(document.querySelectorAll('[role=dialog], .modal, .ds-modal'));" & ¬
		"  var scope = roots.length ? roots[roots.length - 1] : document;" & ¬
		"  var btns = Array.from(scope.querySelectorAll('button, input[type=submit], [role=button]'));" & ¬
		"  for (var i = 0; i < btns.length; i++) {" & ¬
		"    var btn = btns[i];" & ¬
		"    var txt = ((btn.textContent || btn.value || '') + ' ' + (btn.getAttribute('title') || '') + ' ' + (btn.getAttribute('data-testid') || '')).toLowerCase();" & ¬
		"    if (/log out|confirm|yes/.test(txt) && !btn.disabled) {" & ¬
		"      btn.click();" & ¬
		"      return 'confirmed-logout:' + txt.trim().slice(0, 50);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  return 'no-confirmation-needed';" & ¬
		"})();"
end jsConfirmLogoutEverywhere

on jsProbeSaveButton()
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=personal-information-card__saveChanges-btn]');" & ¬
		"  if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true') return 'ready';" & ¬
		"  return 'missing';" & ¬
		"})();"
end jsProbeSaveButton

on waitForSaveButton(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set saveState to "missing"
		try
			set saveState to safariJS(jsProbeSaveButton()) as text
		end try
		if saveState is "ready" then return "ready"
		delay 0.5
	end repeat
	return "timeout"
end waitForSaveButton

on jsProbeLogoutButton()
	return "(function () {" & ¬
		"  return document.querySelector('button[data-testid=log-out-everywhere-button]') ? 'ready' : 'missing';" & ¬
		"})();"
end jsProbeLogoutButton

on waitForLogoutButton(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set logoutState to "missing"
		try
			set logoutState to safariJS(jsProbeLogoutButton()) as text
		end try
		if logoutState is "ready" then return "ready"
		delay 0.5
	end repeat
	return "timeout"
end waitForLogoutButton

on jsProbeEmailField()
	-- Keep JS in a handler; avoid short var names like "st" (Script Editor rejects them).
	return "(function () {" & ¬
		"  var el = document.querySelector('input[data-testid=personal-information-card__emailAddress]');" & ¬
		"  if (el) return 'ready';" & ¬
		"  return 'missing';" & ¬
		"})();"
end jsProbeEmailField

on jsDumpPageState()
	-- Compact page snapshot for failure investigation (no passwords).
	-- Avoid backslash escapes in this AppleScript string (Script Editor / osascript).
	return "(function () {" & ¬
		"  function has(sel) { return !!document.querySelector(sel); }" & ¬
		"  function btnState(sel) {" & ¬
		"    var el = document.querySelector(sel);" & ¬
		"    if (!el) return 'missing';" & ¬
		"    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return 'disabled';" & ¬
		"    return 'enabled';" & ¬
		"  }" & ¬
		"  function flat(text) {" & ¬
		"    var out = String(text || '');" & ¬
		"    out = out.split(String.fromCharCode(10)).join(' ');" & ¬
		"    out = out.split(String.fromCharCode(13)).join(' ');" & ¬
		"    out = out.split(String.fromCharCode(9)).join(' ');" & ¬
		"    while (out.indexOf('  ') >= 0) out = out.split('  ').join(' ');" & ¬
		"    return out.replace(/^ +| +$/g, '');" & ¬
		"  }" & ¬
		"  var snippet = flat((document.body && document.body.innerText) || '').slice(0, 220);" & ¬
		"  var emailEl = document.querySelector('input[data-testid=personal-information-card__emailAddress]');" & ¬
		"  var emailVal = emailEl ? String(emailEl.value || '').slice(0, 80) : '';" & ¬
		"  return JSON.stringify({" & ¬
		"    href: location.href," & ¬
		"    ready: document.readyState," & ¬
		"    title: document.title || ''," & ¬
		"    emailField: has('input[data-testid=personal-information-card__emailAddress]')," & ¬
		"    emailValue: emailVal," & ¬
		"    saveBtn: btnState('button[data-testid=personal-information-card__saveChanges-btn]')," & ¬
		"    logoutBtn: btnState('button[data-testid=log-out-everywhere-button]')," & ¬
		"    captcha: has('iframe[src*=hcaptcha.com]')," & ¬
		"    loginUser: has('input[name=username], input[autocomplete=username]')," & ¬
		"    loginPass: has('input[name=password], input[type=password]')," & ¬
		"    mfa: has('input[name=code], input[autocomplete=one-time-code], input[inputmode=numeric]')," & ¬
		"    bodySnippet: snippet" & ¬
		"  });" & ¬
		"})();"
end jsDumpPageState

on waitForEmailField(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set emailFieldState to "missing"
		try
			set emailFieldState to safariJS(jsProbeEmailField()) as text
		end try
		if emailFieldState is "ready" then return "ready"
		delay 0.5
	end repeat
	return "timeout"
end waitForEmailField

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
	set loginPhase to "unknown"
	repeat while (current date) < deadline
		try
			set loginPhase to safariJS(jsProbePhase()) as text
		on error
			set loginPhase to "unknown"
		end try
		if loginPhase is "bad_creds" then return "bad_creds"
		if loginPhase is "captcha" then return "captcha"
		if loginPhase is "mfa" then return "mfa"
		if loginPhase is "account" then return "account"
		if loginPhase is "logged_in" then return "logged_in"
		delay 0.8
	end repeat
	return "timeout"
end waitForPostLogin

on parseArgs(argv)
	set opts to {}
	set i to 1
	repeat while i <= (count of argv)
		set a to item i of argv as text
		-- Skip osascript's end-of-options marker if present
		if a is "--" then
			set i to i + 1
		else if a starts with "--" then
			-- NOTE: do not use variable name "key" — reserved in AppleScript (-10006)
			set flagName to text 3 thru -1 of a
			if flagName is "skip-email-change" or flagName is "batch" then
				set end of opts to {flagName, "1"}
			else if i < (count of argv) then
				set i to i + 1
				set end of opts to {flagName, item i of argv as text}
			else
				set end of opts to {flagName, "1"}
			end if
			set i to i + 1
		else
			set i to i + 1
		end if
	end repeat
	return opts
end parseArgs

on optValue(opts, optName, defaultValue)
	repeat with p in opts
		set k to item 1 of p as text
		if k is optName then return item 2 of p as text
	end repeat
	return defaultValue
end optValue

on optFlag(opts, optName)
	return optValue(opts, optName, "") is "1"
end optFlag

on envOrEmpty(varName)
	try
		set v to system attribute varName
		if v is missing value then return ""
		return v as text
	on error
		return ""
	end try
end envOrEmpty

on taskFileValue(taskFilePath, keyName)
	-- Read one key=value line. Use shell so LF/CR from do shell script is not an issue.
	-- (Splitting AppleScript text on linefeed alone broke and swallowed the whole file.)
	try
		set cmd to "grep -m1 '^" & keyName & "=' " & quoted form of taskFilePath & " | cut -d= -f2-"
		set raw to do shell script cmd
		return raw as text
	on error
		return ""
	end try
end taskFileValue

on scriptDir()
	try
		set envDir to ""
		try
			set envDir to system attribute "TOOL_DIR"
		end try
		if envDir is not "" then return envDir

		-- path to me is Script Editor for .applescript text — only trust if batch runner is there.
		set p to POSIX path of (path to me)
		set d to do shell script "dirname " & quoted form of p
		try
			do shell script "test -f " & quoted form of (d & "/run_safari_batch.py")
			return d
		end try

		set found to findTasksCsvPath()
		if found is not "" then return toolkitRootFromCsv(found)
		return d
	on error
		return do shell script "pwd"
	end try
end scriptDir

on toolkitRootFromCsv(csvPath)
	set parentDir to do shell script "dirname " & quoted form of csvPath
	set baseName to do shell script "basename " & quoted form of parentDir
	if baseName is "data" then
		return do shell script "dirname " & quoted form of parentDir
	end if
	return parentDir
end toolkitRootFromCsv

on findTasksCsvPath()
	-- Desktop downloads often look like:
	--   ~/Desktop/riot-email-update-safari-mac 3/data/tasks.csv
	-- Prefer the last match alphabetically so " 3" wins over older copies.
	-- Uses find_tasks_csv.sh (no backslash escapes inside AppleScript strings).
	try
		set dir to ""
		try
			set envDir to system attribute "TOOL_DIR"
			if envDir is not "" then set dir to envDir
		end try
		if dir is "" then
			set p to POSIX path of (path to me)
			set dir to do shell script "dirname " & quoted form of p
		end if
		set helper to dir & "/find_tasks_csv.sh"
		try
			do shell script "test -x " & quoted form of helper
		on error
			do shell script "chmod +x " & quoted form of helper
		end try
		set found to do shell script "/bin/bash " & quoted form of helper
		return found
	on error
		return ""
	end try
end findTasksCsvPath

on discoverHint()
	set found to findTasksCsvPath()
	if found is not "" then
		set rootDir to toolkitRootFromCsv(found)
		return "BUILD 2026-08-09h" & return & return & "Found your CSV at:" & return & found & return & return & "In Terminal run:" & return & "cd " & quoted form of rootDir & return & "./run_safari_mac.sh" & return & return & "Or double-click RUN_ME.command in that folder."
	end if
	return "BUILD 2026-08-09h" & return & return & "Put accounts in data/tasks.csv inside your Desktop toolkit folder, then run RUN_ME.command or ./run_safari_mac.sh"
end discoverHint

on runBatchFromCsv()
	set csvPath to findTasksCsvPath()
	if csvPath is "" then return false

	set rowCount to 0
	try
		set rowCount to (do shell script "awk 'NR>1 && NF && $0 !~ /^#/ {n++} END{print n+0}' " & quoted form of csvPath) as integer
	end try
	if rowCount is 0 then return false

	set dir to toolkitRootFromCsv(csvPath)

	display dialog "BUILD 2026-08-09h" & return & return & "Found " & rowCount & " account(s) in:" & return & csvPath & return & return & "Run all now via Safari?" buttons {"Cancel", "Run all"} default button "Run all"

	set py to "/usr/bin/python3"
	try
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
	end try

	logLine("Launching batch for " & rowCount & " account(s) from " & csvPath)
	set batchCmd to "cd " & quoted form of dir & " && TOOL_DIR=" & quoted form of dir & " " & quoted form of py & " " & quoted form of (dir & "/run_safari_batch.py") & " " & quoted form of csvPath & " 2>&1"
	try
		set batchOut to do shell script batchCmd
	on error errMsg
		set batchOut to errMsg
	end try
	logLine(batchOut)

	set summary to "Batch finished. See success.txt / failed.txt in:" & return & dir
	try
		set tailOut to do shell script "tail -n 6 " & quoted form of (dir & "/failed.txt") & " 2>/dev/null || true"
		if tailOut is not "" then set summary to summary & return & return & "Recent failures:" & return & tailOut
	end try
	display dialog summary buttons {"OK"} default button "OK"
	return true
end runBatchFromCsv

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

on fetchImapVerifyLink(prefix, sinceEpoch)
	-- Wait for a Riot email titled "Verify Your Email" and return its Verify Email URL.
	try
		set dir to scriptDir()
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
		set helperPath to dir & "/fetch_riot_verify_link.py"
		set cmd to "cd " & quoted form of dir & " && TOOL_DIR=" & quoted form of dir & " " & quoted form of py & " " & quoted form of helperPath & " --prefix " & quoted form of prefix & " --timeout 180 --since-epoch " & quoted form of sinceEpoch & " --subject " & quoted form of "Verify Your Email"
		logLine("IMAP verify-link fetch (" & prefix & ")…")
		set verifyURL to do shell script cmd
		if verifyURL is not "" then
			logLine("IMAP verification link received (" & prefix & ")")
			return verifyURL
		end if
	on error errMsg
		logLine("IMAP verify-link fetch skipped/failed: " & errMsg)
	end try
	return ""
end fetchImapVerifyLink

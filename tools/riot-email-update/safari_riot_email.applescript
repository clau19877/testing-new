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
	set newPass to optValue(opts, "new-password", "")
	set newEmail to optValue(opts, "new-email", "")
	set mfaCode to optValue(opts, "mfa-code", "")
	set verifyCode to optValue(opts, "verify-code", "")
	set skipEmail to optFlag(opts, "skip-email-change")
	set skipPassword to optFlag(opts, "skip-password-change")
	set batchMode to optFlag(opts, "batch")

	-- Primary source for batch: task file written from tasks.csv (reliable).
	set taskFile to optValue(opts, "task-file", "")
	if taskFile is "" then set taskFile to envOrEmpty("SAFARI_TASK_FILE")
	if taskFile is not "" then
		set batchMode to true
		set tfUser to taskFileValue(taskFile, "riot_username")
		set tfPass to taskFileValue(taskFile, "riot_password")
		set tfNewPass to taskFileValue(taskFile, "new_password")
		set tfEmail to taskFileValue(taskFile, "new_email")
		set tfEntry to taskFileValue(taskFile, "entry_url")
		if tfUser is not "" then set riotUser to tfUser
		if tfPass is not "" then set riotPass to tfPass
		if tfNewPass is not "" then set newPass to tfNewPass
		if tfEmail is not "" then set newEmail to tfEmail
		if tfEntry is not "" then set entryURL to tfEntry
		if taskFileValue(taskFile, "skip_email_change") is "1" then set skipEmail to true
		if taskFileValue(taskFile, "skip_password_change") is "1" then set skipPassword to true
	end if

	-- Fall back to env (batch runner also sets these from tasks.csv).
	if riotUser is "" then set riotUser to envOrEmpty("RIOT_USERNAME")
	if riotPass is "" then set riotPass to envOrEmpty("RIOT_PASSWORD")
	if newPass is "" then set newPass to envOrEmpty("NEW_PASSWORD")
	if newEmail is "" then set newEmail to envOrEmpty("NEW_EMAIL")
	if entryURL is defaultEntryURL() then
		set envEntry to envOrEmpty("LOGIN_ENTRY_URL")
		if envEntry is "" then set envEntry to envOrEmpty("LOGIN_URL")
		if envEntry is not "" then set entryURL to envEntry
	end if
	if (not batchMode) and (envOrEmpty("SAFARI_BATCH") is "1") then
		set batchMode to true
	end if
	if (not skipPassword) and (envOrEmpty("SKIP_PASSWORD_CHANGE") is "1") then
		set skipPassword to true
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
	if (not skipPassword) and newPass is "" then
		if batchMode then error "batch mode: no new_password (add new_password column to tasks.csv, or set skip_password_change=1)"
		error "No new password in the task. Check data/tasks.csv new_password column."
	end if

	if skipPassword then
		logLine("Account: " & riotUser & " -> " & newEmail & " (skip password change)")
	else
		logLine("Account: " & riotUser & " -> " & newEmail & " (will change password first)")
	end if
	try
		-- Warm Safari with a normal page before hitting Riot via docs.qq (helps avoid Cloudflare).
		logStep("warmup")
		warmupSafari()

		logStep("open_entry")
		logLine("Opening Safari → docs.qq.com entry…")
		tell application "Safari"
			activate
			set URL of document 1 to entryURL
		end tell
		humanDelay(2.5, 4.5)
		assertNoCloudflare("after_entry")
		waitForContinueReady(20)

		-- Click Continue on the Tencent Docs interstitial (prefer real click; hard-jump is last resort).
		logStep("click_continue")
		logLine("Clicking Continue on interstitial…")
		set continueResult to ""
		set continueAttempt to 0
		repeat while continueAttempt < 3
			set continueAttempt to continueAttempt + 1
			set continueResult to safariJS(jsClickContinue(false))
			logLine("Continue attempt " & continueAttempt & ": " & continueResult)
			if continueResult starts with "clicked:" or continueResult starts with "goto:" then exit repeat
			humanDelay(1.0, 2.0)
		end repeat
		if continueResult does not start with "clicked:" and continueResult does not start with "goto:" then
			logLine("Continue click missed — hard-jump fallback once…")
			set continueResult to safariJS(jsClickContinue(true))
			logLine("Continue fallback: " & continueResult)
		end if
		humanDelay(2.5, 4.5)
		assertNoCloudflare("after_continue")
		waitForRiotLogin(60)

		logStep("fill_login")
		logLine("Waiting for Riot login form…")
		set formReady to waitForLoginForm(45)
		logLine("Login form ready: " & formReady)
		if formReady is not "ready" then error "Riot login form not ready after docs.qq Continue (phase/form timeout)."
		assertNoCloudflare("before_fill_login")
		humanDelay(0.8, 1.8)
		logLine("Filling Riot login form…")
		set fillResult to safariJS(jsFillLogin(riotUser, riotPass))
		logLine("Fill result: " & fillResult)
		humanDelay(0.7, 1.6)

		logStep("click_sign_in")
		logLine("Clicking Sign in…")
		safariJS(jsClickSignIn())

		-- Short post-login wait: succeed as soon as account/email field appears (do not burn 2 minutes).
		set phase to waitForPostLogin(45)
		logLine("Post-login phase: " & phase)

		if phase is "cloudflare" then
			logLine("Cloudflare challenge detected — waiting up to 60s for Safari to pass…")
			set phase to waitForCloudflareClear(60)
			logLine("After Cloudflare wait phase: " & phase)
			if phase is "cloudflare" then
				dumpDebug("cloudflare")
				error "Cloudflare challenge blocked login. Warm Safari manually (browse apple.com / riotgames.com), wait, then re-run. Increase SAFARI_BATCH_DELAY."
			end if
		end if

		if phase is "captcha" then
			if batchMode then error "hCaptcha appeared (batch mode will not wait for manual solve)"
			display dialog "hCaptcha appeared in Safari. Solve it in the Safari window, then click OK." buttons {"OK"} default button 1
			set phase to waitForPostLogin(60)
			logLine("After manual captcha phase: " & phase)
		end if

		if phase is "bad_creds" then
			dumpDebug("bad_creds")
			error "Riot rejected username/password (Check your details and try again). Verify riot_password in tasks.csv."
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
			delay 2
			set phase to waitForPostLogin(45)
			logLine("After MFA phase: " & phase)
		end if

		-- Fast path: if the email field is already on screen, skip long waits / reload.
		set emailReady to safariJS(jsProbeEmailField())
		if emailReady is not "ready" then
			if phase is not "logged_in" and phase is not "account" then
				set cur to ""
				try
					tell application "Safari" to set cur to URL of document 1
				end try
				logLine("Post-login URL fallback: " & cur)
				if cur contains "account.riotgames.com" and cur does not contain "log-in" then
					logLine("Treating as logged in despite phase=" & phase)
				else
					-- Brief extra wait for redirects, then require account host.
					set phase to waitForPostLogin(20)
					logLine("Post-login extra phase: " & phase)
					try
						tell application "Safari" to set cur to URL of document 1
					end try
					if not (cur contains "account.riotgames.com" and cur does not contain "log-in") then
						if phase is not "logged_in" and phase is not "account" then
							error "Login did not reach account.riotgames.com (phase=" & phase & "). Check Safari window."
						end if
					end if
				end if
			end if
		else
			logLine("Email field already visible after login — skipping account reload wait")
		end if

		-- Ensure we are on the account page before password / email edits.
		if emailReady is not "ready" then
			logStep("open_account")
			logLine("Opening account settings…")
			tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
			humanDelay(0.8, 1.5)
		end if

		-- Password change (before email): current / new / confirm → password-card__submit-btn
		if not skipPassword then
			logStep("change_password")
			logLine("Waiting for password-card fields…")
			set pwReady to waitForPasswordFields(20)
			logLine("Password fields ready: " & pwReady)
			if pwReady is not "ready" then
				tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
				humanDelay(1.0, 1.8)
				set pwReady to waitForPasswordFields(20)
				logLine("Password fields ready (retry): " & pwReady)
			end if
			if pwReady is not "ready" then error "Could not find password-card__currentPassword / newPassword / confirmNewPassword."

			logLine("Filling current + new password…")
			set pwFill to safariJS(jsFillPasswordChange(riotPass, newPass))
			logLine(pwFill)
			if pwFill does not contain "filled=1" then error "Failed to fill password-card fields (" & pwFill & ")"
			humanDelay(0.5, 1.0)

			logLine("Waiting for password SAVE / submit button…")
			set pwSaveReady to waitForPasswordSaveButton(15)
			logLine("Password save ready: " & pwSaveReady)
			if pwSaveReady is not "ready" then error "password-card__submit-btn did not become enabled."
			logLine("Clicking password-card__submit-btn…")
			set pwSaveResult to safariJS(jsClickPasswordSave())
			logLine(pwSaveResult)
			if pwSaveResult does not contain "clicked-password-save" then error "Could not click password-card__submit-btn (" & pwSaveResult & ")."
			humanDelay(2.0, 3.5)

			-- Password change may ask for MFA; reuse IMAP when needed.
			set pwPhase to waitForPostLogin(20)
			logLine("Post-password-change phase: " & pwPhase)
			if pwPhase is "mfa" then
				set pwMfa to fetchImapCode("IMAP")
				if pwMfa is "" then
					if batchMode then error "Password change MFA required but no IMAP code"
					set pwMfa to text returned of (display dialog "Enter Riot MFA code for password change:" default answer "")
				end if
				if pwMfa is not "" then
					logLine("Submitting MFA for password change…")
					safariJS(jsSubmitCode(pwMfa, "mfa"))
					humanDelay(2.0, 3.0)
				end if
			end if
			-- Riot typically invalidates the session after a password change.
			-- Switch to the new password and re-login if Safari lands on authenticate.*.
			set riotPass to newPass
			logLine("Password change submitted; watching for session drop…")
			set settleWaited to 0
			repeat while settleWaited < 12
				try
					if safariJS(jsProbeEmailField()) is "ready" then exit repeat
				end try
				try
					if safariJS(jsProbeLoginForm()) is "ready" then
						logLine("Login form appeared after password change (session dropped).")
						exit repeat
					end if
				end try
				delay 0.8
				set settleWaited to settleWaited + 1
			end repeat
			logLine("Restoring account session with new password if needed…")
			ensureAccountSession(riotUser, riotPass, batchMode)
			logLine("Account session ready after password change — continuing to email update…")
		else
			logLine("Skipping password change.")
		end if

		if skipEmail then
			logLine("Skipping email change (--skip-email-change).")
			if not batchMode then display dialog "Logged in via Safari. Email change skipped." buttons {"OK"} default button 1
			logLine("SUCCESS")
			logLine("Debug log: " & logFilePath)
			return "login_ok"
		end if

		logStep("wait_email_field")
		-- Re-check session before email edit (password change / redirect can drop it).
		ensureAccountSession(riotUser, riotPass, batchMode)
		logLine("Waiting for personal-information-card__emailAddress…")
		set emailReady to waitForEmailField(20)
		logLine("Email field ready: " & emailReady)
		if emailReady is not "ready" then
			safariJS(jsClickEmailControl())
			humanDelay(0.6, 1.2)
			set emailReady to waitForEmailField(12)
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

		-- Page often navigates after Save; JS may return empty (-2763). Keep going.
		set captchaState to ""
		try
			set captchaState to safariJS(jsCaptchaVisible())
		end try
		logLine("Post-save captcha state: " & captchaState)
		if captchaState is "captcha" then
			if batchMode then
				logLine("hCaptcha challenge visible during save — waiting up to 60s for auto/solve…")
				set waited to 0
				repeat while waited < 60
					set captchaState to ""
					try
						set captchaState to safariJS(jsCaptchaVisible())
					end try
					if captchaState is not "captcha" then exit repeat
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
		logLine("Fetching verification link via IMAP (prefer recipient=" & newEmail & ")…")
		set verifyLink to fetchImapVerifyLink("NEW_IMAP", verifySinceEpoch, newEmail)
		if verifyLink is "" then set verifyLink to fetchImapVerifyLink("IMAP", verifySinceEpoch, newEmail)

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
		-- Session may have expired during the IMAP wait — re-login with current password if needed.
		ensureAccountSession(riotUser, riotPass, batchMode)
		set logoutReady to waitForLogoutButton(20)
		logLine("Logout button ready: " & logoutReady)
		if logoutReady is not "ready" then error "Could not find log-out-everywhere-button after verification."
		logLine("Clicking LOG OUT EVERYWHERE…")
		set logoutResult to safariJS(jsClickLogoutEverywhere())
		logLine(logoutResult)
		if logoutResult does not contain "clicked-logout" then error "Could not click LOG OUT EVERYWHERE (" & logoutResult & ")."
		delay 0.8

		-- Confirm modal: button[data-testid=modal_close-btn] title="Confirm"
		logLine("Waiting for Confirm modal (modal_close-btn)…")
		set confirmReady to waitForLogoutConfirmButton(15)
		logLine("Confirm button ready: " & confirmReady)
		if confirmReady is not "ready" then error "LOG OUT EVERYWHERE Confirm modal (modal_close-btn) did not appear."
		logLine("Clicking Confirm…")
		set confirmResult to safariJS(jsConfirmLogoutEverywhere())
		logLine(confirmResult)
		if confirmResult does not contain "confirmed-logout" then error "Could not click Confirm on logout modal (" & confirmResult & ")."
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
	logLine("BUILD 2026-08-09t")
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
	-- Never call logLine inside "tell application Safari" (Apple Events routing breaks).
	logLine("--- debug dump (" & reasonLabel & ") ---")
	set curURL to ""
	set curName to ""
	try
		tell application "Safari"
			try
				set curURL to URL of document 1
			end try
			try
				set curName to name of document 1
			end try
		end tell
	on error errMsg
		logLine("safari dump failed: " & errMsg)
	end try
	if curURL is not "" then
		logLine("safari.url=" & curURL)
	else
		logLine("safari.url=<unavailable>")
	end if
	if curName is not "" then
		logLine("safari.title=" & curName)
	else
		logLine("safari.title=<unavailable>")
	end if
	try
		set pageProbe to safariJS(jsDumpPageState())
		logLine("page.probe=" & pageProbe)
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
	-- Always return text. During page navigations Safari often yields "no result" (-2763).
	tell application "Safari"
		try
			set jsResult to do JavaScript js in document 1
			if jsResult is missing value then return ""
			try
				return jsResult as text
			on error
				return ""
			end try
		on error errMsg number errNum
			if errNum is -1728 or errMsg contains "JavaScript from Apple Events" or errMsg contains "Allow JavaScript" then
				error "Enable Safari → Develop → Allow JavaScript from Apple Events, then re-run. (" & errMsg & ")"
			end if
			-- -2763: expression did not return a result (page navigating / empty JS return)
			if errNum is -2763 then return ""
			if errMsg contains "沒有傳回結果" or errMsg contains "did not return a result" then return ""
			error errMsg number errNum
		end try
	end tell
end safariJS

-- JS builders: keep double-quotes out of AppleScript string literals.
-- Use only single quotes in the JS source text below.

on jsClickContinue(allowHardJump)
	-- Prefer a real Continue / Riot link click. Hard location.href jump is last resort
	-- (drops referrer and looks more bot-like to Cloudflare / Riot).
	set bs to backslashChar()
	set jumpFlag to "false"
	if allowHardJump then set jumpFlag to "true"
	return "(function (allowJump) {" & ¬
		"  try { window.scrollBy(0, 120 + Math.floor(Math.random() * 180)); } catch (e0) {}" & ¬
		"  const candidates = Array.from(document.querySelectorAll('a,button'));" & ¬
		"  for (const el of candidates) {" & ¬
		"    const t = (el.textContent || '').trim();" & ¬
		"    const href = (el.getAttribute('href') || '');" & ¬
		"    if (/account" & bs & ".riotgames" & bs & ".com|authenticate" & bs & ".riotgames" & bs & ".com/i.test(href)" & ¬
		"        || /^Continue/i.test(t) || t.indexOf('Continue') === 0) {" & ¬
		"      try { el.scrollIntoView({ block: 'center' }); } catch (e1) {}" & ¬
		"      el.click();" & ¬
		"      return 'clicked:' + (t || href).slice(0, 80);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  if (allowJump) {" & ¬
		"    try {" & ¬
		"      const u = new URL(location.href);" & ¬
		"      const target = u.searchParams.get('url');" & ¬
		"      if (target) { location.href = target; return 'goto:' + target; }" & ¬
		"    } catch (e2) {}" & ¬
		"  }" & ¬
		"  return 'no-continue';" & ¬
		"})(" & jumpFlag & ");"
end jsClickContinue

on jsProbeContinueReady()
	set bs to backslashChar()
	return "(function () {" & ¬
		"  const candidates = Array.from(document.querySelectorAll('a,button'));" & ¬
		"  for (const el of candidates) {" & ¬
		"    const t = (el.textContent || '').trim();" & ¬
		"    const href = (el.getAttribute('href') || '');" & ¬
		"    if (/account" & bs & ".riotgames" & bs & ".com|authenticate" & bs & ".riotgames" & bs & ".com/i.test(href)" & ¬
		"        || /^Continue/i.test(t) || t.indexOf('Continue') === 0) return 'ready';" & ¬
		"  }" & ¬
		"  try {" & ¬
		"    const u = new URL(location.href);" & ¬
		"    if (u.searchParams.get('url')) return 'ready';" & ¬
		"  } catch (e) {}" & ¬
		"  return 'missing';" & ¬
		"})();"
end jsProbeContinueReady

on jsProbeLoginForm()
	-- Must NOT treat account password-card / email fields as the Riot sign-in form.
	return "(function () {" & ¬
		"  var host = (location.hostname || '').toLowerCase();" & ¬
		"  if (host.indexOf('riotgames.com') < 0 && host.indexOf('riot.com') < 0) return 'wrong-host';" & ¬
		"  if (document.querySelector('input[data-testid=personal-information-card__emailAddress]')) return 'account-page';" & ¬
		"  if (document.querySelector('input[data-testid=password-card__currentPassword]')) return 'account-page';" & ¬
		"  var userEl = document.querySelector('input[name=username], input[autocomplete=username]');" & ¬
		"  if (!userEl) return 'missing';" & ¬
		"  var passEl = document.querySelector('input[name=password]');" & ¬
		"  if (!passEl) {" & ¬
		"    passEl = Array.from(document.querySelectorAll('input[type=password]')).find(function (el) {" & ¬
		"      var id = (el.getAttribute('data-testid') || '');" & ¬
		"      return id.indexOf('password-card') < 0;" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (userEl && passEl) return 'ready';" & ¬
		"  return 'missing';" & ¬
		"})();"
end jsProbeLoginForm

on jsProbeHostname()
	return "(function () {" & ¬
		"  return (location.hostname || '').toLowerCase();" & ¬
		"})();"
end jsProbeHostname

on jsProbeChallenge()
	-- Cloudflare / managed bot interstitial signals.
	return "(function () {" & ¬
		"  var title = (document.title || '').toLowerCase();" & ¬
		"  var body = ((document.body && document.body.innerText) || '').toLowerCase();" & ¬
		"  var blob = title + ' ' + body;" & ¬
		"  if (document.querySelector('#challenge-form, .cf-browser-verification, .cf-challenge, iframe[src*=challenges.cloudflare.com], iframe[src*=turnstile], #cf-challenge-running, .challenge-platform'))" & ¬
		"    return 'cloudflare';" & ¬
		"  if (blob.indexOf('just a moment') >= 0) return 'cloudflare';" & ¬
		"  if (blob.indexOf('checking your browser') >= 0) return 'cloudflare';" & ¬
		"  if (blob.indexOf('attention required') >= 0) return 'cloudflare';" & ¬
		"  if (blob.indexOf('cf-browser-verification') >= 0) return 'cloudflare';" & ¬
		"  if (blob.indexOf('enable javascript and cookies') >= 0 && blob.indexOf('cloudflare') >= 0) return 'cloudflare';" & ¬
		"  return 'ok';" & ¬
		"})();"
end jsProbeChallenge

on jsWarmScroll()
	return "(function () {" & ¬
		"  try {" & ¬
		"    window.scrollBy(0, 80 + Math.floor(Math.random() * 220));" & ¬
		"    return 'scrolled';" & ¬
		"  } catch (e) { return 'no-scroll'; }" & ¬
		"})();"
end jsWarmScroll

on jsFillLogin(userName, passText)
	-- Only touch Riot sign-in fields — never password-card / personal-information inputs.
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
		"  if (document.querySelector('input[data-testid=password-card__currentPassword], input[data-testid=personal-information-card__emailAddress]'))" & ¬
		"    return JSON.stringify({ user: false, pass: false, skipped: 'account-page', href: location.href });" & ¬
		"  const userEl = document.querySelector('input[name=username], input[autocomplete=username]');" & ¬
		"  let passEl = document.querySelector('input[name=password]');" & ¬
		"  if (!passEl) {" & ¬
		"    passEl = Array.from(document.querySelectorAll('input[type=password]')).find(function (el) {" & ¬
		"      var id = (el.getAttribute('data-testid') || '');" & ¬
		"      return id.indexOf('password-card') < 0;" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
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
	-- bad_creds: only exact Riot reject phrase / error UI (not generic 'try again' on login pages).
	-- onLogin must ignore password-card fields on account.riotgames.com (those are type=password too).
	return "(function () {" & ¬
		"  const href = location.href || '';" & ¬
		"  const body = (document.body && document.body.innerText || '').toLowerCase();" & ¬
		"  const onAccountWidgets = !!(document.querySelector('input[data-testid=personal-information-card__emailAddress], input[data-testid=password-card__currentPassword], button[data-testid=log-out-everywhere-button]'));" & ¬
		"  const hasLoginUser = !!document.querySelector('input[name=username], input[autocomplete=username]');" & ¬
		"  const hasLoginPass = !!document.querySelector('input[name=password]') || Array.from(document.querySelectorAll('input[type=password]')).some(function (el) {" & ¬
		"    return ((el.getAttribute('data-testid') || '').indexOf('password-card') < 0);" & ¬
		"  });" & ¬
		"  const onLogin = !onAccountWidgets && hasLoginUser && hasLoginPass;" & ¬
		"  function badCredsVisible() {" & ¬
		"    const nodes = Array.from(document.querySelectorAll('[role=alert], [data-testid*=error], [data-testid*=Error], .error-message, [class*=error-message], [class*=ErrorMessage]'));" & ¬
		"    for (const el of nodes) {" & ¬
		"      const t = ((el.innerText || el.textContent || '') + '').toLowerCase();" & ¬
		"      if (/check your details and try again|incorrect username|incorrect password|invalid (username|password|credentials)|wrong password/.test(t)) return true;" & ¬
		"    }" & ¬
		"    if (onLogin && /check your details and try again/.test(body)) return true;" & ¬
		"    return false;" & ¬
		"  }" & ¬
		"  	if (badCredsVisible()) return 'bad_creds';" & ¬
		"  var title = (document.title || '').toLowerCase();" & ¬
		"  var blob = title + ' ' + body;" & ¬
		"  if (document.querySelector('#challenge-form, .cf-browser-verification, .cf-challenge, iframe[src*=challenges.cloudflare.com], iframe[src*=turnstile], #cf-challenge-running')" & ¬
		"      || blob.indexOf('just a moment') >= 0 || blob.indexOf('checking your browser') >= 0" & ¬
		"      || (blob.indexOf('attention required') >= 0 && blob.indexOf('cloudflare') >= 0))" & ¬
		"    return 'cloudflare';" & ¬
		"  if (document.querySelector('input[data-testid=personal-information-card__emailAddress]'))" & ¬
		"    return 'account';" & ¬
		"  if (onAccountWidgets) return 'account';" & ¬
		"  var frames = Array.from(document.querySelectorAll('iframe[src*=hcaptcha.com]'));" & ¬
		"  for (var fi = 0; fi < frames.length; fi++) {" & ¬
		"    var fr = frames[fi];" & ¬
		"    var box = fr.getBoundingClientRect();" & ¬
		"    if (box.width > 50 && box.height > 50 && fr.offsetParent !== null && fr.src.indexOf('frame=challenge') >= 0)" & ¬
		"      return 'captcha';" & ¬
		"  }" & ¬
		"  if (/code|verification|authenticate|two-factor|2fa|email you/.test(body)" & ¬
		"      && document.querySelector('input[name=code], input[autocomplete=one-time-code], input[inputmode=numeric]'))" & ¬
		"    return 'mfa';" & ¬
		"  var host = (location.hostname || '').toLowerCase();" & ¬
		"  var onRiot = host.indexOf('riotgames.com') >= 0 || host.indexOf('riot.com') >= 0;" & ¬
		"  if (onLogin) return 'login';" & ¬
		"  if (onRiot && /account" & bs & ".riotgames" & bs & ".com/.test(host) && href.indexOf('log-in') < 0 && href.indexOf('login') < 0 && href.indexOf('oauth2') < 0)" & ¬
		"    return 'account';" & ¬
		"  if (onRiot && !onLogin && host.indexOf('account.') === 0)" & ¬
		"    return 'logged_in';" & ¬
		"  if (hasLoginUser || (onRiot && (host.indexOf('authenticate.') === 0 || host.indexOf('auth.') === 0) && hasLoginPass))" & ¬
		"    return 'login';" & ¬
		"  if (onRiot && !onLogin) return 'logged_in';" & ¬
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

on jsProbePasswordFields()
	return "(function () {" & ¬
		"  var cur = document.querySelector('input[data-testid=password-card__currentPassword]');" & ¬
		"  var neu = document.querySelector('input[data-testid=password-card__newPassword]');" & ¬
		"  var conf = document.querySelector('input[data-testid=password-card__confirmNewPassword]');" & ¬
		"  if (cur && neu && conf) return 'ready';" & ¬
		"  return 'missing';" & ¬
		"})();"
end jsProbePasswordFields

on waitForPasswordFields(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set pwState to "missing"
		try
			set pwState to safariJS(jsProbePasswordFields()) as text
		end try
		if pwState is "ready" then return "ready"
		delay 0.35
	end repeat
	return "timeout"
end waitForPasswordFields

on jsFillPasswordChange(currentPass, nextPass)
	-- password-card__currentPassword / newPassword / confirmNewPassword
	return "(function (curPass, newPass) {" & ¬
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
		"  var cur = document.querySelector('input[data-testid=password-card__currentPassword]');" & ¬
		"  var neu = document.querySelector('input[data-testid=password-card__newPassword]');" & ¬
		"  var conf = document.querySelector('input[data-testid=password-card__confirmNewPassword]');" & ¬
		"  if (!cur || !neu || !conf) return 'filled=0 missing-field';" & ¬
		"  try { cur.scrollIntoView({ block: 'center' }); } catch (e0) {}" & ¬
		"  var okCur = setNative(cur, curPass);" & ¬
		"  var okNew = setNative(neu, newPass);" & ¬
		"  var okConf = setNative(conf, newPass);" & ¬
		"  return (okCur && okNew && okConf ? 'filled=1 ok' : 'filled=0 mismatch')" & ¬
		"    + ' cur=' + okCur + ' new=' + okNew + ' conf=' + okConf;" & ¬
		"})(" & jsonString(currentPass) & ", " & jsonString(nextPass) & ");"
end jsFillPasswordChange

on jsProbePasswordSaveButton()
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=password-card__submit-btn]');" & ¬
		"  if (!btn) {" & ¬
		"    var cands = Array.from(document.querySelectorAll('button[type=submit], button'));" & ¬
		"    btn = cands.find(function (b) {" & ¬
		"      var t = ((b.getAttribute('data-testid') || '') + ' ' + (b.getAttribute('title') || '') + ' ' + (b.textContent || '')).toLowerCase();" & ¬
		"      return t.indexOf('password-card__submit') >= 0 || (t.indexOf('save') >= 0 && t.indexOf('password') >= 0);" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!btn) return 'missing';" & ¬
		"  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'disabled';" & ¬
		"  return 'ready';" & ¬
		"})();"
end jsProbePasswordSaveButton

on waitForPasswordSaveButton(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set pwSaveState to "missing"
		try
			set pwSaveState to safariJS(jsProbePasswordSaveButton()) as text
		end try
		if pwSaveState is "ready" then return "ready"
		delay 0.35
	end repeat
	return "timeout"
end waitForPasswordSaveButton

on jsClickPasswordSave()
	-- Riot password save: button[data-testid=password-card__submit-btn]
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=password-card__submit-btn]');" & ¬
		"  if (!btn) {" & ¬
		"    var cands = Array.from(document.querySelectorAll('button[type=submit], button'));" & ¬
		"    btn = cands.find(function (b) {" & ¬
		"      var t = ((b.getAttribute('data-testid') || '') + ' ' + (b.getAttribute('title') || '') + ' ' + (b.textContent || '')).toLowerCase();" & ¬
		"      return t.indexOf('password-card__submit') >= 0 || ((t.indexOf('save') >= 0 || t.indexOf('change password') >= 0) && t.indexOf('email') < 0);" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!btn) return 'no-password-save-button';" & ¬
		"  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'password-save-disabled';" & ¬
		"  try { btn.scrollIntoView({ block: 'center' }); } catch (e1) {}" & ¬
		"  btn.click();" & ¬
		"  return 'clicked-password-save:' + ((btn.getAttribute('title') || btn.textContent || '').trim().slice(0, 40));" & ¬
		"})();"
end jsClickPasswordSave

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
	-- Exact Riot control:
	-- <button type="submit" data-testid="log-out-everywhere-button" title="LOG OUT EVERYWHERE">
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=log-out-everywhere-button]');" & ¬
		"  if (!btn) {" & ¬
		"    var cands = Array.from(document.querySelectorAll('button[type=submit], button'));" & ¬
		"    btn = cands.find(function (b) {" & ¬
		"      var t = ((b.getAttribute('title') || '') + ' ' + (b.textContent || '')).toLowerCase();" & ¬
		"      return t.indexOf('log out everywhere') >= 0;" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!btn) return 'no-logout-button';" & ¬
		"  try { btn.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e1) {}" & ¬
		"  try { btn.focus(); } catch (e2) {}" & ¬
		"  btn.click();" & ¬
		"  return 'clicked-logout:' + ((btn.getAttribute('title') || btn.textContent || '').trim().slice(0, 40));" & ¬
		"})();"
end jsClickLogoutEverywhere

on jsProbeLogoutConfirmButton()
	-- Confirm modal after LOG OUT EVERYWHERE:
	-- <button type="submit" data-testid="modal_close-btn" title="Confirm">
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=modal_close-btn]');" & ¬
		"  if (!btn) return 'missing';" & ¬
		"  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'disabled';" & ¬
		"  var t = ((btn.getAttribute('title') || '') + ' ' + (btn.textContent || '')).toLowerCase();" & ¬
		"  if (t.indexOf('confirm') < 0 && t.indexOf('log out') < 0) return 'missing';" & ¬
		"  return 'ready';" & ¬
		"})();"
end jsProbeLogoutConfirmButton

on waitForLogoutConfirmButton(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set confirmState to "missing"
		try
			set confirmState to safariJS(jsProbeLogoutConfirmButton()) as text
		end try
		if confirmState is "ready" then return "ready"
		delay 0.4
	end repeat
	return "timeout"
end waitForLogoutConfirmButton

on jsConfirmLogoutEverywhere()
	-- Prefer exact Riot Confirm control: data-testid=modal_close-btn title=Confirm
	return "(function () {" & ¬
		"  var btn = document.querySelector('button[data-testid=modal_close-btn]');" & ¬
		"  if (btn && !btn.disabled) {" & ¬
		"    var label = ((btn.getAttribute('title') || '') + ' ' + (btn.textContent || '')).trim();" & ¬
		"    btn.click();" & ¬
		"    return 'confirmed-logout:modal_close-btn:' + label.slice(0, 40);" & ¬
		"  }" & ¬
		"  var roots = Array.from(document.querySelectorAll('[role=dialog], .modal, .ds-modal, [class*=modal]'));" & ¬
		"  var scope = roots.length ? roots[roots.length - 1] : document;" & ¬
		"  var btns = Array.from(scope.querySelectorAll('button[type=submit], button, input[type=submit]'));" & ¬
		"  for (var i = 0; i < btns.length; i++) {" & ¬
		"    var el = btns[i];" & ¬
		"    var txt = ((el.textContent || el.value || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.getAttribute('data-testid') || '')).toLowerCase();" & ¬
		"    if ((txt.indexOf('confirm') >= 0 || txt.indexOf('log out everywhere') >= 0) && !el.disabled) {" & ¬
		"      el.click();" & ¬
		"      return 'confirmed-logout:fallback:' + txt.trim().slice(0, 50);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  return 'no-confirm-button';" & ¬
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

on humanDelay(minSec, maxSec)
	-- Jittered pause so pacing is less robotic.
	set lo to minSec as real
	set hi to maxSec as real
	if hi < lo then set hi to lo
	set span to hi - lo
	set pick to lo + (span * (random number from 0 to 1000) / 1000.0)
	delay pick
end humanDelay

on warmupSafari()
	-- Prime a normal Safari document before docs.qq / Riot (reduces cold-start CF hits).
	logLine("Warming up Safari with a normal page…")
	tell application "Safari"
		activate
		try
			if (count of documents) is 0 then
				make new document with properties {URL:"https://www.apple.com/"}
			else
				set URL of document 1 to "https://www.apple.com/"
			end if
		on error
			make new document with properties {URL:"https://www.apple.com/"}
		end try
	end tell
	humanDelay(3.0, 6.0)
	try
		logLine(safariJS(jsWarmScroll()))
	end try
	humanDelay(1.2, 2.8)
	assertNoCloudflare("warmup")
	logLine("Warm-up complete")
end warmupSafari

on assertNoCloudflare(whereLabel)
	set challengeState to "ok"
	try
		set challengeState to safariJS(jsProbeChallenge())
	end try
	if challengeState is "cloudflare" then
		logLine("Cloudflare signal at " & whereLabel & " — waiting briefly…")
		set cleared to waitForCloudflareClear(45)
		if cleared is "cloudflare" then
			dumpDebug("cloudflare_" & whereLabel)
			error "Cloudflare challenge at " & whereLabel & ". Browse manually in Safari until the page loads, then re-run. Use longer SAFARI_BATCH_DELAY."
		end if
	end if
end assertNoCloudflare

on waitForContinueReady(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set continueState to "missing"
		try
			set continueState to safariJS(jsProbeContinueReady()) as text
		end try
		if continueState is "ready" then return "ready"
		try
			if safariJS(jsProbeChallenge()) is "cloudflare" then
				if waitForCloudflareClear(20) is "cloudflare" then error "Cloudflare on docs.qq interstitial before Continue."
			end if
		end try
		delay 0.5
	end repeat
	return "timeout"
end waitForContinueReady

on waitForLoginForm(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set formState to "missing"
		try
			set formState to safariJS(jsProbeLoginForm()) as text
		end try
		if formState is "ready" then return "ready"
		try
			if safariJS(jsProbeChallenge()) is "cloudflare" then
				if waitForCloudflareClear(30) is "cloudflare" then return "cloudflare"
			end if
		end try
		delay 0.6
	end repeat
	return "timeout"
end waitForLoginForm

on waitForRiotLogin(timeoutSec)
	-- IMPORTANT: match location.hostname only. The docs.qq entry URL embeds
	-- account.riotgames.com in ?url= and must NOT count as landed.
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set hostName to ""
		try
			set hostName to safariJS(jsProbeHostname()) as text
		end try
		if hostName ends with "riotgames.com" or hostName ends with "riot.com" then
			humanDelay(0.8, 1.6)
			return
		end if
		try
			if safariJS(jsProbeChallenge()) is "cloudflare" then
				logLine("Cloudflare while waiting for Riot host…")
				if waitForCloudflareClear(30) is "cloudflare" then
					error "Cloudflare challenge while opening Riot login from docs.qq."
				end if
			end if
		end try
		delay 0.6
	end repeat
	error "Timed out waiting for Riot login host after docs.qq Continue (still not on *.riotgames.com)."
end waitForRiotLogin

on waitForCloudflareClear(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set challengeState to "ok"
		try
			set challengeState to safariJS(jsProbeChallenge()) as text
		end try
		if challengeState is not "cloudflare" then
			-- Also accept a non-challenge phase from the main probe.
			set loginPhase to "unknown"
			try
				set loginPhase to safariJS(jsProbePhase()) as text
			end try
			if loginPhase is not "cloudflare" then return loginPhase
		end if
		delay 1.2
	end repeat
	return "cloudflare"
end waitForCloudflareClear

on waitForPostLogin(timeoutSec)
	set deadline to (current date) + timeoutSec
	set loginPhase to "unknown"
	set badCredHits to 0
	set cfHits to 0
	set loginHits to 0
	repeat while (current date) < deadline
		-- Fast success: email field means we can enter the new address immediately.
		try
			if safariJS(jsProbeEmailField()) is "ready" then return "account"
		end try
		try
			set loginPhase to safariJS(jsProbePhase()) as text
		on error
			set loginPhase to "unknown"
		end try
		-- Require the reject message to stick (avoid one-frame false positives).
		if loginPhase is "bad_creds" then
			set badCredHits to badCredHits + 1
			if badCredHits >= 3 then return "bad_creds"
		else
			set badCredHits to 0
		end if
		if loginPhase is "cloudflare" then
			set cfHits to cfHits + 1
			if cfHits >= 2 then return "cloudflare"
		else
			set cfHits to 0
		end if
		if loginPhase is "captcha" then return "captcha"
		if loginPhase is "mfa" then return "mfa"
		if loginPhase is "account" then return "account"
		if loginPhase is "logged_in" then return "logged_in"
		-- "login" only after it sticks — right after Sign in the form is still briefly visible.
		if loginPhase is "login" then
			set loginHits to loginHits + 1
			if loginHits >= 8 then return "login"
		else
			set loginHits to 0
		end if
		delay 0.35
	end repeat
	return "timeout"
end waitForPostLogin

on ensureAccountSession(riotUser, riotPass, batchMode)
	-- After a password change Riot often invalidates cookies and redirects to
	-- authenticate.riotgames.com. Re-login with the (new) password until the
	-- account email field is visible again.
	set attempt to 0
	repeat while attempt < 3
		set attempt to attempt + 1
		try
			if safariJS(jsProbeEmailField()) is "ready" then
				logLine("ensureAccountSession: already on account page")
				return "ok"
			end if
		end try

		set phaseNow to "unknown"
		set formNow to "missing"
		try
			set phaseNow to safariJS(jsProbePhase()) as text
		end try
		try
			set formNow to safariJS(jsProbeLoginForm()) as text
		end try
		logLine("ensureAccountSession attempt " & attempt & " phase=" & phaseNow & " form=" & formNow)

		if phaseNow is "cloudflare" then
			set phaseNow to waitForCloudflareClear(45)
			logLine("ensureAccountSession after CF: " & phaseNow)
		end if

		if formNow is "ready" or phaseNow is "login" then
			logLine("Session dropped — signing in again…")
			set formReady to waitForLoginForm(30)
			logLine("Re-login form: " & formReady)
			if formReady is not "ready" then
				tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
				humanDelay(1.5, 2.5)
				set formReady to waitForLoginForm(30)
			end if
			if formReady is not "ready" then error "Session dropped after password change but login form never appeared."

			set fillResult to safariJS(jsFillLogin(riotUser, riotPass))
			logLine("Re-login fill: " & fillResult)
			if fillResult contains "account-page" then
				-- Race: account widgets came back while we probed login.
				humanDelay(0.8, 1.2)
			else
				humanDelay(0.5, 1.0)
				safariJS(jsClickSignIn())
				set phaseNow to waitForPostLogin(45)
				logLine("Re-login post phase: " & phaseNow)
				if phaseNow is "bad_creds" then error "Re-login failed after password change (bad password?). Check new_password in tasks.csv."
				if phaseNow is "captcha" then
					if batchMode then error "hCaptcha during re-login after password change"
					display dialog "hCaptcha during re-login. Solve it in Safari, then click OK." buttons {"OK"} default button 1
					set phaseNow to waitForPostLogin(60)
				end if
				if phaseNow is "mfa" then
					set mfaCode to fetchImapCode("IMAP")
					if mfaCode is "" then
						if batchMode then error "MFA required during re-login after password change"
						set mfaCode to text returned of (display dialog "Enter Riot MFA code (re-login):" default answer "")
					end if
					if mfaCode is not "" then
						safariJS(jsSubmitCode(mfaCode, "mfa"))
						humanDelay(2.0, 3.0)
						set phaseNow to waitForPostLogin(45)
						logLine("Re-login after MFA phase: " & phaseNow)
					end if
				end if
			end if
		else
			logLine("Opening account.riotgames.com to restore session…")
			tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
			humanDelay(1.2, 2.2)
			set phaseNow to waitForPostLogin(25)
			logLine("ensureAccountSession navigate phase: " & phaseNow)
			if phaseNow is "mfa" then
				set mfaCode to fetchImapCode("IMAP")
				if mfaCode is not "" then
					safariJS(jsSubmitCode(mfaCode, "mfa"))
					humanDelay(2.0, 3.0)
				end if
			end if
		end if

		try
			if safariJS(jsProbeEmailField()) is "ready" then
				logLine("ensureAccountSession: email field ready")
				return "ok"
			end if
		end try
		-- Brief settle; next loop will re-login if authenticate form is up.
		humanDelay(1.0, 1.8)
	end repeat

	-- Final forced open + short wait for diagnostics.
	try
		tell application "Safari" to set URL of document 1 to "https://account.riotgames.com/"
	end try
	humanDelay(1.5, 2.5)
	try
		if safariJS(jsProbeEmailField()) is "ready" then return "ok"
	end try
	error "Could not restore account session after password change (still not on account email form)."
end ensureAccountSession

on waitForEmailField(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		set emailFieldState to "missing"
		try
			set emailFieldState to safariJS(jsProbeEmailField()) as text
		end try
		if emailFieldState is "ready" then return "ready"
		delay 0.35
	end repeat
	return "timeout"
end waitForEmailField

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
			if flagName is "skip-email-change" or flagName is "skip-password-change" or flagName is "batch" then
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

on resolveToolkitDir()
	-- Locate the modern toolkit folder (has run_safari_batch.py).
	-- Prefer TOOL_DIR / path-to-me, then Desktop *safari-mac* via find_tasks_csv.sh.
	try
		set envDir to ""
		try
			set envDir to system attribute "TOOL_DIR"
		end try
		if envDir is not "" then
			try
				do shell script "test -f " & quoted form of (envDir & "/run_safari_batch.py")
				return envDir
			end try
		end if

		-- path to me is Script Editor for .applescript text — only trust if batch runner is there.
		set p to POSIX path of (path to me)
		set d to do shell script "dirname " & quoted form of p
		try
			do shell script "test -f " & quoted form of (d & "/run_safari_batch.py")
			return d
		end try

		-- Bootstrap: ask any Desktop find_tasks_csv.sh for the modern toolkit root.
		set bootstrap to do shell script "find " & quoted form of (POSIX path of (path to desktop folder)) & " " & quoted form of ((POSIX path of (path to home folder)) & "Downloads") & " -type f -name find_tasks_csv.sh 2>/dev/null | sort | tail -n1"
		if bootstrap is "" then return d
		try
			do shell script "chmod +x " & quoted form of bootstrap
		end try
		set discovered to do shell script "/bin/bash " & quoted form of bootstrap & " --toolkit-root"
		if discovered is not "" then return discovered
		return do shell script "dirname " & quoted form of bootstrap
	on error
		return do shell script "pwd"
	end try
end resolveToolkitDir

on scriptDir()
	return resolveToolkitDir()
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
	-- Prefer CSV beside the running toolkit, then Desktop *safari-mac* CSVs.
	try
		set dir to resolveToolkitDir()
		set helper to dir & "/find_tasks_csv.sh"
		try
			do shell script "test -f " & quoted form of helper
		on error
			return ""
		end try
		try
			do shell script "chmod +x " & quoted form of helper
		end try
		set found to do shell script "/bin/bash " & quoted form of helper & " " & quoted form of dir
		return found
	on error
		return ""
	end try
end findTasksCsvPath

on discoverHint()
	set found to findTasksCsvPath()
	if found is not "" then
		set rootDir to scriptDir()
		return "BUILD 2026-08-09t" & return & return & "Found your CSV at:" & return & found & return & return & "In Terminal run:" & return & "cd " & quoted form of rootDir & return & "./run_safari_mac.sh" & return & return & "Or double-click RUN_ME.command in that folder." & return & return & "(Do not use an older Desktop/riotemail copy of the scripts.)"
	end if
	return "BUILD 2026-08-09t" & return & return & "Put accounts in data/tasks.csv inside your Desktop toolkit folder, then run RUN_ME.command or ./run_safari_mac.sh"
end discoverHint

on runBatchFromCsv()
	set csvPath to findTasksCsvPath()
	if csvPath is "" then return false

	set rowCount to 0
	try
		set rowCount to (do shell script "awk 'NR>1 && NF && $0 !~ /^#/ {n++} END{print n+0}' " & quoted form of csvPath) as integer
	end try
	if rowCount is 0 then return false

	-- Always run the NEW toolkit scripts (scriptDir), even if CSV lives elsewhere.
	set dir to scriptDir()
	try
		do shell script "test -f " & quoted form of (dir & "/run_safari_batch.py")
	on error
		set dir to toolkitRootFromCsv(csvPath)
	end try

	display dialog "BUILD 2026-08-09t" & return & return & "Found " & rowCount & " account(s) in:" & return & csvPath & return & return & "Scripts:" & return & dir & return & return & "Run all now via Safari?" & return & return & "To hard-stop later: double-click STOP_BATCH.command" buttons {"Cancel", "Run all"} default button "Run all"

	set py to "/usr/bin/python3"
	try
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
	end try

	logLine("Launching batch for " & rowCount & " account(s) from " & csvPath)
	logLine("Using toolkit scripts: " & dir)
	-- Clear any previous stop flag; write PID so STOP_BATCH.command / Script Editor Stop can kill it.
	try
		do shell script "rm -f " & quoted form of (dir & "/.safari_batch_stop"); do shell script "chmod +x " & quoted form of (dir & "/stop_safari_batch.sh") & " " & quoted form of (dir & "/STOP_BATCH.command") & " 2>/dev/null || true"
	end try
	-- Background the batch; poll until it exits OR stop flag appears (Script Editor Stop / STOP_BATCH).
	set batchCmd to "cd " & quoted form of dir & " && rm -f .safari_batch_stop && TOOL_DIR=" & quoted form of dir & " " & quoted form of py & " " & quoted form of (dir & "/run_safari_batch.py") & " " & quoted form of csvPath & " > " & quoted form of (dir & "/.safari_batch_console.log") & " 2>&1 & echo $! > " & quoted form of (dir & "/.safari_batch.pid") & "; pid=$(cat " & quoted form of (dir & "/.safari_batch.pid") & "); while kill -0 \"$pid\" 2>/dev/null; do if [ -f " & quoted form of (dir & "/.safari_batch_stop") & " ]; then /bin/bash " & quoted form of (dir & "/stop_safari_batch.sh") & " >/dev/null 2>&1; break; fi; sleep 0.5; done; wait \"$pid\" 2>/dev/null; exit 0"
	try
		do shell script batchCmd
		set batchOut to do shell script "tail -n 80 " & quoted form of (dir & "/.safari_batch_console.log") & " 2>/dev/null || true"
	on error errMsg number errNum
		-- Stop button in Script Editor (-128) or other failure: kill the batch immediately.
		logLine("Batch interrupted (" & errNum & "): " & errMsg)
		try
			do shell script "/bin/bash " & quoted form of (dir & "/stop_safari_batch.sh")
		end try
		set batchOut to errMsg
		try
			set batchOut to batchOut & return & (do shell script "tail -n 40 " & quoted form of (dir & "/.safari_batch_console.log") & " 2>/dev/null || true")
		end try
	end try
	logLine(batchOut)

	set summary to "Batch finished (or stopped). See success.txt / failed.txt in:" & return & dir & return & return & "Hard-stop anytime with STOP_BATCH.command"
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

on fetchImapVerifyLink(prefix, sinceEpoch, recipientEmail)
	-- Wait for a Riot email titled "Verify Your Email" and return its Verify Email URL.
	try
		set dir to scriptDir()
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
		set helperPath to dir & "/fetch_riot_verify_link.py"
		set cmd to "cd " & quoted form of dir & " && TOOL_DIR=" & quoted form of dir & " NEW_EMAIL=" & quoted form of recipientEmail & " " & quoted form of py & " " & quoted form of helperPath & " --prefix " & quoted form of prefix & " --timeout 240 --since-epoch " & quoted form of sinceEpoch & " --subject " & quoted form of "Verify Your Email" & " --recipient " & quoted form of recipientEmail
		logLine("IMAP verify-link fetch (" & prefix & ", to=" & recipientEmail & ")…")
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

-- Riot account email update via local macOS Safari.
--
-- Why Safari: on a real Mac Safari session, Riot often skips hCaptcha.
-- Opens account.riotgames.com directly, signs in, handles MFA, updates email.
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
-- Cooperative stop: STOP_BATCH.command / run_safari_batch.py write this file.
property stopFlagPath : ""


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
		-- Per-row CSV column change_password: 1=on, 0=off (overrides default).
		set changePwRaw to taskFileValue(taskFile, "change_password")
		if changePwRaw is "0" then
			set skipPassword to true
		else if changePwRaw is "1" then
			set skipPassword to false
		end if
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
	-- Global override wins over CSV change_password.
	if envOrEmpty("SKIP_PASSWORD_CHANGE") is "1" then
		set skipPassword to true
	end if
	-- Env CHANGE_PASSWORD=0/1 also accepted for one-off runs.
	set envChangePw to envOrEmpty("CHANGE_PASSWORD")
	if envChangePw is "0" then
		set skipPassword to true
	else if envChangePw is "1" then
		if envOrEmpty("SKIP_PASSWORD_CHANGE") is not "1" then set skipPassword to false
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
		if batchMode then error "batch mode: no new_password (set new_password, or change_password=0 / SKIP_PASSWORD_CHANGE=1)"
		error "No new password in the task. Check data/tasks.csv new_password column (or set change_password=0)."
	end if

	if skipPassword then
		logLine("Account: " & riotUser & " -> " & newEmail & " (email first; password change OFF)")
	else
		logLine("Account: " & riotUser & " -> " & newEmail & " (email first, then password)")
	end if
	try
		-- Warm Safari with a normal page before hitting Riot (helps avoid Cloudflare).
		logStep("warmup")
		warmupSafari()

		-- Critical for batch: previous account session must not leak into this login.
		logStep("pre_account_logout")
		forceLogoutSession("before_account")

		logStep("open_entry")
		logLine("Opening Safari → " & entryURL)
		ensureSafariDocument()
		with timeout of 45 seconds
			tell application "Safari"
				set URL of document 1 to entryURL
			end tell
		end timeout
		humanDelay(2.5, 4.5)
		assertNoCloudflare("after_entry")

		-- Optional legacy path: only if LOGIN_ENTRY_URL still points at docs.qq.
		set entryHost to ""
		try
			set entryHost to safariJS(jsProbeHostname()) as text
		end try
		if entryHost contains "docs.qq.com" or entryURL contains "docs.qq.com" then
			logLine("Legacy docs.qq entry detected — clicking Continue…")
			waitForContinueReady(20)
			logStep("click_continue")
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
				logLine("Continue click missed — opening account.riotgames.com…")
				safariGoTo("https://account.riotgames.com/")
			end if
			humanDelay(2.0, 3.5)
			assertNoCloudflare("after_continue")
		end if

		waitForRiotLogin(60)

		-- If a prior session survived logout, account.riotgames.com has no login form.
		if sessionLooksLoggedIn() then
			logLine("Entry landed on logged-in account page — clearing session before login…")
			forceLogoutSession("after_entry_still_logged_in")
		end if

		logStep("fill_login")
		logLine("Waiting for Riot login form…")
		if not sessionLooksLoggedOut() then
			safariGoTo("https://account.riotgames.com/")
			humanDelay(1.2, 2.0)
			if not sessionLooksLoggedOut() then
				safariGoTo("https://authenticate.riotgames.com/")
				humanDelay(1.2, 2.0)
			end if
		end if
		set formReady to waitForLoginForm(45)
		logLine("Login form ready: " & formReady)
		if formReady is not "ready" then
			logLine("Login form missing — full logout + account.riotgames.com retry…")
			forceLogoutSession("login_form_retry")
			safariGoTo("https://account.riotgames.com/")
			humanDelay(2.0, 3.0)
			if not sessionLooksLoggedOut() then
				safariGoTo("https://authenticate.riotgames.com/")
				humanDelay(1.5, 2.5)
			end if
			set formReady to waitForLoginForm(35)
			logLine("Login form ready (retry): " & formReady)
		end if
		if formReady is not "ready" then error "Riot login form not ready after logout/account entry (phase/form timeout)."
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
					set cur to safariCurrentURL()
				end try
				logLine("Post-login URL fallback: " & cur)
				if cur contains "account.riotgames.com" and cur does not contain "log-in" then
					logLine("Treating as logged in despite phase=" & phase)
				else
					-- Brief extra wait for redirects, then require account host.
					set phase to waitForPostLogin(20)
					logLine("Post-login extra phase: " & phase)
					try
						set cur to safariCurrentURL()
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
			safariGoTo("https://account.riotgames.com/")
			humanDelay(0.8, 1.5)
		end if

		-- ===== Email change FIRST =====
		if skipEmail then
			logLine("Skipping email change (--skip-email-change / skip_email_change=1).")
		else
			logStep("wait_email_field")
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
						waitTick(3)
						set waited to waited + 3
					end repeat
				else
					display dialog "Solve the hCaptcha in Safari, then click OK." buttons {"OK"} default button 1
				end if
			end if
			delay 2

			logStep("imap_verify_link")
			logLine("Fetching verification link via IMAP (prefer recipient=" & newEmail & ")…")
			set verifyLink to fetchImapVerifyLink("NEW_IMAP", verifySinceEpoch, newEmail)
			if verifyLink is "" then set verifyLink to fetchImapVerifyLink("IMAP", verifySinceEpoch, newEmail)

			if verifyLink is not "" then
				logLine("Opening verification link in Safari…")
				safariGoTo(verifyLink)
				waitTick(4)
				logLine(safariJS(jsClickVerifyOnLanding()))
				waitTick(2)
			else
				if batchMode then
					error "No Verify Your Email link found via IMAP within timeout."
				else
					display dialog "No verification link found via IMAP. Verify the email manually in Safari, then click OK." buttons {"OK"} default button 1
				end if
			end if
			logLine("Email change + verify complete.")
		end if

		-- ===== Password change SECOND (change_password=1) =====
		if skipPassword then
			logLine("Skipping password change (change_password=0 / SKIP_PASSWORD_CHANGE=1).")
		else
			logStep("change_password")
			set oldPassForVerify to riotPass
			logLine("Returning to account page before password change…")
			ensureAccountSession(riotUser, riotPass, batchMode)
			logLine("Waiting for password-card fields…")
			set pwReady to waitForPasswordFields(20)
			logLine("Password fields ready: " & pwReady)
			if pwReady is not "ready" then
				safariGoTo("https://account.riotgames.com/")
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

			-- Do NOT logout yet. Wait for Riot to finish the password update.
			logStep("wait_password_change_outcome")
			logLine("Waiting for password-change confirmation (success / error / session drop)…")
			humanDelay(3.0, 4.5)
			set pwPhase to waitForPostLogin(25)
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

			set pwOutcome to waitForPasswordChangeOutcome(55)
			logLine("Password-change outcome: " & pwOutcome)
			if pwOutcome is "error" then error "Riot rejected the password change (error banner). Check current/new password in tasks.csv."
			if pwOutcome is "timeout" then
				logLine("WARNING: no explicit success banner — will hard-verify via forced re-login with new_password.")
			else
				logLine("Password UI outcome accepted (" & pwOutcome & "); hard-verifying with new password before logout…")
			end if

			-- HARD VERIFY: end the current session, then prove new_password works.
			-- Prior builds treated "still on account page" as success and logged out too early.
			logStep("verify_password_change")
			logLine("Forcing logout before new-password verification (do not keep old session)…")
			forceLogoutSession("before_new_password_verify")
			logLine("Signing in with NEW password to confirm change…")
			set newLogin to signInWithCredentials(riotUser, newPass, batchMode)
			logLine("New-password login result: " & newLogin)
			if newLogin does not start with "ok" then
				logLine("New password login failed — checking whether old password still works…")
				forceLogoutSession("after_new_password_fail")
				set oldLogin to signInWithCredentials(riotUser, oldPassForVerify, batchMode)
				logLine("Old-password login result: " & oldLogin)
				if oldLogin starts with "ok" then
					error "Password change did not take effect (old password still works; new password rejected). Logout was blocked."
				end if
				error "Password change not confirmed (cannot sign in with new_password: " & newLogin & "; old also failed: " & oldLogin & ")."
			end if

			set riotPass to newPass
			logLine("Password change confirmed via re-login with new_password — safe to logout.")
		end if

		if skipEmail and skipPassword then
			if not batchMode then display dialog "Logged in via Safari. Email + password changes skipped." buttons {"OK"} default button 1
			logLine("SUCCESS")
			logLine("Debug log: " & logFilePath)
			return "login_ok"
		end if

		-- Riotbar account menu Logout (data-testid=riotbar:account:link-logout).
		logStep("riotbar_logout")
		logLine("Returning to account page for riotbar Logout…")
		ensureAccountSession(riotUser, riotPass, batchMode)
		set logoutReady to waitForRiotbarLogout(20)
		logLine("Riotbar Logout ready: " & logoutReady)
		if logoutReady is not "ready" then error "Could not find riotbar Logout link (data-testid=riotbar:account:link-logout)."
		logLine("Clicking riotbar Logout…")
		set logoutResult to safariJS(jsClickRiotbarLogout())
		logLine(logoutResult)
		if logoutResult does not contain "clicked-riotbar-logout" then error "Could not click riotbar Logout (" & logoutResult & ")."
		set waitState to waitUntilLoggedOut(25)
		logLine("Post riotbar-logout wait: " & waitState)
		if waitState is "still_logged_in" then error "Still logged in after riotbar Logout."

		-- Make sure Safari is logged out before the batch starts the next CSV row.
		logStep("verify_logged_out")
		forceLogoutSession("after_success")
		logLine("Logged out — safe to start next account.")

		if not batchMode then
			display dialog "Finished this account (email first, optional password, logged out)." buttons {"OK"} default button 1
		end if
		logLine("SUCCESS")
		logLine("Debug log: " & logFilePath)
		return "done"
	on error errMsg number errNum
		logLine("STEP FAILED: " & errMsg & " (" & errNum & ")")
		dumpDebug("failure")
		-- Best-effort logout so a failure does not leave the next account stuck.
		try
			logStep("logout_after_failure")
			forceLogoutSession("after_failure")
		end try
		logLine("Debug log: " & logFilePath)
		error errMsg number errNum
	end try
end run


-- ============== helpers ==============

on defaultEntryURL()
	return "https://account.riotgames.com/"
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
	try
		set stopFlagPath to scriptDir() & "/.safari_batch_stop"
	end try
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
	logLine("BUILD 2026-08-12f")
	logLine("log file → " & logFilePath)
	if logAccountLabel is not "" then logLine("account=" & logAccountLabel)
	try
		logLine("tool dir=" & scriptDir())
	end try
	try
		logLine("cwd=" & (do shell script "pwd"))
	end try
	if safariShouldStealFocus() then
		logLine("Safari focus: ON (SAFARI_STEAL_FOCUS=1) — Safari may jump to front")
	else
		logLine("Safari focus: OFF (background) — Safari stays behind your other work")
		logLine("Tip: put Safari on another Desktop/Space; do not click its window while batching.")
	end if
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
		with timeout of 15 seconds
			tell application "Safari"
				try
					set curURL to URL of document 1
				end try
				try
					set curName to name of document 1
				end try
			end tell
		end timeout
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

on assertNotStopped()
	-- Exit promptly when STOP_BATCH / Ctrl+C wrote .safari_batch_stop.
	try
		if stopFlagPath is "" then set stopFlagPath to scriptDir() & "/.safari_batch_stop"
		do shell script "/bin/test -f " & quoted form of stopFlagPath
		error "Batch stop requested (.safari_batch_stop)."
	on error errMsg number errNum
		if errMsg contains "Batch stop requested" then error errMsg number errNum
	end try
end assertNotStopped

on waitTick(pauseSec)
	-- Short pause that still lets Stop / stop-flag land between iterations.
	assertNotStopped()
	set secs to pauseSec as real
	if secs < 0 then set secs to 0
	delay secs
end waitTick

on safariJS(js)
	-- Always return text. During page navigations Safari often yields "no result" (-2763).
	-- with timeout: Safari Apple Events can hang forever without this.
	assertNotStopped()
	try
		ensureSafariDocument()
	end try
	with timeout of 30 seconds
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
				if errNum is -1712 then error "Safari JavaScript timed out (30s)." number errNum
				-- Permission errors mention Apple Events / JavaScript — not bare "document 1" missing.
				if errMsg contains "JavaScript from Apple Events" or errMsg contains "Allow JavaScript" then
					error "Enable Safari → Develop → Allow JavaScript from Apple Events, then re-run. (" & errMsg & ")"
				end if
				-- Missing/closed tab: recover as empty instead of aborting the whole account.
				if errNum is -1728 or errMsg contains "document 1" or errMsg contains "無法取得" then return ""
				-- -2763: expression did not return a result (page navigating / empty JS return)
				if errNum is -2763 then return ""
				if errMsg contains "沒有傳回結果" or errMsg contains "did not return a result" then return ""
				if errMsg contains "尚未定義變數" or errMsg contains "is not defined" then return ""
				error errMsg number errNum
			end try
		end tell
	end timeout
end safariJS

on safariShouldStealFocus()
	-- Default OFF so batch can run while you use other apps.
	-- Set SAFARI_STEAL_FOCUS=1 only if you need Safari brought to the front.
	set flag to envOrEmpty("SAFARI_STEAL_FOCUS")
	if flag is "1" or flag is "true" or flag is "yes" then return true
	return false
end safariShouldStealFocus

on safariActivateIfNeeded()
	-- launch = start Safari without forcing it frontmost (macOS).
	-- activate = steal keyboard/mouse focus (annoying during batch).
	with timeout of 20 seconds
		tell application "Safari"
			if my safariShouldStealFocus() then
				activate
			else
				launch
			end if
		end tell
	end timeout
end safariActivateIfNeeded

on ensureSafariDocument()
	-- Guarantees document 1 exists (batch failures sometimes close the only tab).
	-- Does not bring Safari to the front unless SAFARI_STEAL_FOCUS=1.
	safariActivateIfNeeded()
	with timeout of 30 seconds
		tell application "Safari"
			if (count of documents) is 0 then
				make new document with properties {URL:"https://www.apple.com/"}
			end if
		end tell
	end timeout
end ensureSafariDocument

on safariGoTo(destURL)
	-- Navigate document 1 with a hard Apple Event ceiling.
	assertNotStopped()
	ensureSafariDocument()
	with timeout of 45 seconds
		tell application "Safari"
			set URL of document 1 to destURL
		end tell
	end timeout
end safariGoTo

on sessionLooksLoggedIn()
	try
		if safariJS(jsProbeRiotbarLogout()) is "ready" then return true
	end try
	try
		if safariJS(jsProbeLogoutButton()) is "ready" then return true
	end try
	try
		if safariJS(jsProbeEmailField()) is "ready" then return true
	end try
	try
		if safariJS(jsProbePasswordFields()) is "ready" then return true
	end try
	return false
end sessionLooksLoggedIn

on sessionLooksLoggedOut()
	try
		if safariJS(jsProbeLoginForm()) is "ready" then return true
	end try
	return false
end sessionLooksLoggedOut

on waitUntilLoggedOut(timeoutSec)
	-- Stay on the current page after Confirm; do not navigate away mid-logout.
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		if sessionLooksLoggedOut() then return "login_form"
		if not sessionLooksLoggedIn() then return "cleared"
		waitTick(0.8)
	end repeat
	if sessionLooksLoggedOut() then return "login_form"
	if sessionLooksLoggedIn() then return "still_logged_in"
	return "unknown"
end waitUntilLoggedOut

on forceLogoutSession(whereLabel)
	-- Verify logout actually completed. Prior builds clicked Confirm then navigated
	-- away too fast; Riot session stayed alive and the next account never saw a
	-- login form (hard-jump to account.riotgames.com kept showing the account page).
	logLine("Ensuring logged out (" & whereLabel & ")…")
	try
		ensureSafariDocument()
		set attempt to 0
		repeat while attempt < 4
			set attempt to attempt + 1
			assertNotStopped()
			safariGoTo("https://account.riotgames.com/")
			humanDelay(1.2, 2.2)
			if sessionLooksLoggedOut() then
				logLine("Already on login form (" & whereLabel & " #" & attempt & ").")
				exit repeat
			end if
			if sessionLooksLoggedIn() then
				logLine("Active Riot session (" & whereLabel & " #" & attempt & ") — riotbar Logout…")
				set logoutResult to safariJS(jsClickRiotbarLogout())
				logLine(logoutResult)
				if logoutResult contains "clicked-riotbar-logout" then
					set waitState to waitUntilLoggedOut(25)
					logLine("Post-logout wait (" & whereLabel & " #" & attempt & "): " & waitState)
					if waitState is "login_form" or waitState is "cleared" then exit repeat
				else
					logLine("Riotbar Logout click missed (" & logoutResult & ") — trying auth logout URL…")
				end if
			else
				logLine("Account page ambiguous (" & whereLabel & " #" & attempt & ") — auth logout URL…")
			end if
			try
				safariGoTo("https://authenticate.riotgames.com/logout")
				humanDelay(1.5, 2.5)
			end try
			if sessionLooksLoggedOut() then
				logLine("Login form after auth logout URL (" & whereLabel & ").")
				exit repeat
			end if
		end repeat

		-- Always finish on the auth login host (not account.riotgames.com).
		safariGoTo("https://authenticate.riotgames.com/")
		humanDelay(1.5, 2.5)
		if sessionLooksLoggedOut() then
			logLine("Logout verified — login form ready (" & whereLabel & ").")
		else if sessionLooksLoggedIn() then
			logLine("WARNING: still logged in after logout attempts (" & whereLabel & ").")
		else
			logLine("Logout cleanup finished (" & whereLabel & ") — waiting for form at auth host.")
		end if
	on error errMsg
		logLine("Logout cleanup warning (" & whereLabel & "): " & errMsg)
	end try
end forceLogoutSession

on safariCurrentURL()
	with timeout of 15 seconds
		tell application "Safari"
			try
				return URL of document 1
			on error
				return ""
			end try
		end tell
	end timeout
end safariCurrentURL

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
		"    // Never hard-jump to account.riotgames.com while a prior session may still" & ¬
		"    // be logged in — that page has no login form. Prefer the auth login host." & ¬
		"    const authLogin = 'https://authenticate.riotgames.com/';" & ¬
		"    try {" & ¬
		"      const u = new URL(location.href);" & ¬
		"      const target = u.searchParams.get('url') || '';" & ¬
		"      if (/authenticate" & bs & ".riotgames" & bs & ".com/i.test(target)) {" & ¬
		"        location.href = target; return 'goto:' + target;" & ¬
		"      }" & ¬
		"    } catch (e2) {}" & ¬
		"    location.href = authLogin;" & ¬
		"    return 'goto:' + authLogin;" & ¬
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
		waitTick(0.35)
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
		waitTick(0.35)
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

on jsRiotbarLogoutSelector()
	-- a[data-testid="riotbar:account:link-logout"]
	return "a[data-testid=" & quote & "riotbar:account:link-logout" & quote & "]"
end jsRiotbarLogoutSelector

on jsOpenRiotbarAccountMenu()
	-- Logout link lives in the account dropdown; open it if needed.
	return "(function () {" & ¬
		"  var sel = 'a[data-testid=" & quote & "riotbar:account:link-logout" & quote & "]';" & ¬
		"  var link = document.querySelector(sel);" & ¬
		"  if (link && link.offsetParent !== null) return 'menu-already-open';" & ¬
		"  var triggers = [" & ¬
		"    '[data-testid=" & quote & "riotbar:account:button" & quote & "]'," & ¬
		"    '[data-testid=" & quote & "riotbar:account:toggle" & quote & "]'," & ¬
		"    '[data-testid*=" & quote & "riotbar:account" & quote & "]'," & ¬
		"    'button[class*=riotbar-account]'," & ¬
		"    '[class*=riotbar-account-dropdown]'," & ¬
		"    'button[aria-haspopup=true]'" & ¬
		"  ];" & ¬
		"  for (var i = 0; i < triggers.length; i++) {" & ¬
		"    var nodes = Array.from(document.querySelectorAll(triggers[i]));" & ¬
		"    for (var j = 0; j < nodes.length; j++) {" & ¬
		"      var el = nodes[j];" & ¬
		"      var tid = (el.getAttribute('data-testid') || '').toLowerCase();" & ¬
		"      if (tid.indexOf('link-logout') >= 0) continue;" & ¬
		"      try { el.click(); } catch (e0) { continue; }" & ¬
		"      return 'opened-menu:' + (tid || el.className || 'trigger').toString().slice(0, 60);" & ¬
		"    }" & ¬
		"  }" & ¬
		"  return 'no-account-menu';" & ¬
		"})();"
end jsOpenRiotbarAccountMenu

on jsProbeRiotbarLogout()
	return "(function () {" & ¬
		"  var sel = 'a[data-testid=" & quote & "riotbar:account:link-logout" & quote & "]';" & ¬
		"  var link = document.querySelector(sel);" & ¬
		"  if (link) return 'ready';" & ¬
		"  return 'missing';" & ¬
		"})();"
end jsProbeRiotbarLogout

on waitForRiotbarLogout(timeoutSec)
	set deadline to (current date) + timeoutSec
	repeat while (current date) < deadline
		try
			safariJS(jsOpenRiotbarAccountMenu())
		end try
		set logoutState to "missing"
		try
			set logoutState to safariJS(jsProbeRiotbarLogout()) as text
		end try
		if logoutState is "ready" then return "ready"
		waitTick(0.5)
	end repeat
	return "timeout"
end waitForRiotbarLogout

on jsClickRiotbarLogout()
	-- <a data-testid="riotbar:account:link-logout">…Logout…</a>
	return "(function () {" & ¬
		"  var sel = 'a[data-testid=" & quote & "riotbar:account:link-logout" & quote & "]';" & ¬
		"  var link = document.querySelector(sel);" & ¬
		"  if (!link) {" & ¬
		"    var triggers = Array.from(document.querySelectorAll('[data-testid*=" & quote & "riotbar:account" & quote & "], button[class*=riotbar-account], [class*=riotbar-account-dropdown]'));" & ¬
		"    for (var i = 0; i < triggers.length; i++) {" & ¬
		"      try { triggers[i].click(); } catch (e0) {}" & ¬
		"    }" & ¬
		"    link = document.querySelector(sel);" & ¬
		"  }" & ¬
		"  if (!link) {" & ¬
		"    var cands = Array.from(document.querySelectorAll('a.riotbar-account-link, a.riotbar-account-action, a'));" & ¬
		"    link = cands.find(function (a) {" & ¬
		"      var t = ((a.textContent || '') + ' ' + (a.getAttribute('data-testid') || '')).toLowerCase();" & ¬
		"      return t.indexOf('logout') >= 0 && t.indexOf('everywhere') < 0;" & ¬
		"    }) || null;" & ¬
		"  }" & ¬
		"  if (!link) return 'no-riotbar-logout';" & ¬
		"  try { link.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e1) {}" & ¬
		"  link.click();" & ¬
		"  return 'clicked-riotbar-logout:' + ((link.textContent || link.getAttribute('data-testid') || '').trim().slice(0, 40));" & ¬
		"})();"
end jsClickRiotbarLogout

on jsProbePasswordChangeResult()
	-- success / error / form-cleared after password-card save
	return "(function () {" & ¬
		"  var text = ((document.body && document.body.innerText) || '').toLowerCase();" & ¬
		"  if (/password (has been )?(updated|changed|saved)|successfully (changed|updated) (your )?password|your password was (updated|changed)|password (update|change) successful/.test(text)) return 'success';" & ¬
		"  if (/(current )?password (is )?(incorrect|invalid|wrong)|could not (change|update) (your )?password|password change failed|passwords? (do not|don't) match|does not meet|too weak/.test(text)) return 'error';" & ¬
		"  var alertNodes = Array.from(document.querySelectorAll('[role=alert], [aria-live], .alert, [class*=toast], [class*=notification], [class*=banner], [class*=Snackbar], [class*=snackbar]'));" & ¬
		"  for (var i = 0; i < alertNodes.length; i++) {" & ¬
		"    var t = ((alertNodes[i].innerText || alertNodes[i].textContent || '')).toLowerCase();" & ¬
		"    if (!t) continue;" & ¬
		"    if (t.indexOf('password') >= 0 && (t.indexOf('success') >= 0 || t.indexOf('updated') >= 0 || t.indexOf('changed') >= 0 || t.indexOf('saved') >= 0)) return 'success';" & ¬
		"    if (t.indexOf('password') >= 0 && (t.indexOf('error') >= 0 || t.indexOf('fail') >= 0 || t.indexOf('incorrect') >= 0 || t.indexOf('invalid') >= 0)) return 'error';" & ¬
		"  }" & ¬
		"  var cur = document.querySelector('input[data-testid=password-card__currentPassword]');" & ¬
		"  var neu = document.querySelector('input[data-testid=password-card__newPassword]');" & ¬
		"  var conf = document.querySelector('input[data-testid=password-card__confirmNewPassword]');" & ¬
		"  var btn = document.querySelector('button[data-testid=password-card__submit-btn]');" & ¬
		"  if (cur && neu && conf) {" & ¬
		"    var empty = !(cur.value || '') && !(neu.value || '') && !(conf.value || '');" & ¬
		"    var disabled = !btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true';" & ¬
		"    if (empty && disabled) return 'form_cleared';" & ¬
		"  }" & ¬
		"  return 'unknown';" & ¬
		"})();"
end jsProbePasswordChangeResult

on waitForPasswordChangeOutcome(timeoutSec)
	-- Poll until Riot shows success/error, clears the form, or drops to login.
	-- Never treat "still logged in on account page" alone as success.
	set deadline to (current date) + timeoutSec
	set minWaitUntil to (current date) + 5
	repeat while (current date) < deadline
		assertNotStopped()
		try
			if safariJS(jsProbeLoginForm()) is "ready" then return "session_dropped"
		end try
		set pageResult to "unknown"
		try
			set pageResult to safariJS(jsProbePasswordChangeResult()) as text
		end try
		if pageResult is "error" then return "error"
		if pageResult is "success" then return "success"
		if pageResult is "form_cleared" and (current date) > minWaitUntil then return "form_cleared"
		waitTick(0.7)
	end repeat
	return "timeout"
end waitForPasswordChangeOutcome

on verifyPasswordChangeSuccess()
	-- Legacy helper — hard verify now uses signInWithCredentials after forced logout.
	try
		if safariJS(jsProbeEmailField()) is not "ready" then return "fail:no-account-email-field"
	on error
		return "fail:no-account-email-field"
	end try
	set pageResult to "unknown"
	try
		set pageResult to safariJS(jsProbePasswordChangeResult()) as text
	end try
	if pageResult is "error" then return "fail:password-error-banner"
	if pageResult is "success" then return "ok:success-banner"
	if pageResult is "form_cleared" then return "ok:form-cleared"
	return "fail:no-success-signal"
end verifyPasswordChangeSuccess

on signInWithCredentials(riotUser, passText, batchMode)
	-- Assumes session was cleared. Signs in and waits for account email field.
	-- Returns "ok:…" or "fail:…" (does not throw on bad credentials).
	logLine("signInWithCredentials: opening login…")
	safariGoTo("https://account.riotgames.com/")
	humanDelay(1.5, 2.5)
	if not sessionLooksLoggedOut() then
		safariGoTo("https://authenticate.riotgames.com/")
		humanDelay(1.2, 2.0)
	end if
	set formReady to waitForLoginForm(40)
	logLine("signInWithCredentials form: " & formReady)
	if formReady is not "ready" then
		forceLogoutSession("signin_form_missing")
		safariGoTo("https://authenticate.riotgames.com/")
		humanDelay(1.5, 2.5)
		set formReady to waitForLoginForm(35)
		logLine("signInWithCredentials form (retry): " & formReady)
	end if
	if formReady is not "ready" then return "fail:no-login-form"

	set fillResult to safariJS(jsFillLogin(riotUser, passText))
	logLine("signInWithCredentials fill: " & fillResult)
	if fillResult contains "account-page" then
		try
			if safariJS(jsProbeEmailField()) is "ready" then return "ok:already-account"
		end try
	end if
	humanDelay(0.5, 1.0)
	safariJS(jsClickSignIn())
	set phaseNow to waitForPostLogin(50)
	logLine("signInWithCredentials phase: " & phaseNow)
	if phaseNow is "bad_creds" then return "fail:bad_creds"
	if phaseNow is "cloudflare" then
		set phaseNow to waitForCloudflareClear(45)
		logLine("signInWithCredentials after CF: " & phaseNow)
	end if
	if phaseNow is "captcha" then
		if batchMode then return "fail:captcha"
		display dialog "hCaptcha during password verify login. Solve it in Safari, then click OK." buttons {"OK"} default button 1
		set phaseNow to waitForPostLogin(60)
	end if
	if phaseNow is "mfa" then
		set mfaCode to fetchImapCode("IMAP")
		if mfaCode is "" then
			if batchMode then return "fail:mfa"
			set mfaCode to text returned of (display dialog "Enter Riot MFA code (password verify):" default answer "")
		end if
		if mfaCode is not "" then
			safariJS(jsSubmitCode(mfaCode, "mfa"))
			humanDelay(2.0, 3.0)
			set phaseNow to waitForPostLogin(45)
			logLine("signInWithCredentials after MFA: " & phaseNow)
		end if
	end if
	if phaseNow is "bad_creds" then return "fail:bad_creds"

	set emailWait to waitForEmailField(35)
	logLine("signInWithCredentials email field: " & emailWait)
	if emailWait is "ready" then return "ok:account"
	try
		if safariJS(jsProbeEmailField()) is "ready" then return "ok:account"
	end try
	safariGoTo("https://account.riotgames.com/")
	humanDelay(1.5, 2.5)
	set emailWait to waitForEmailField(25)
	if emailWait is "ready" then return "ok:account-retry"
	if safariJS(jsProbeLoginForm()) is "ready" then
		set phaseRetry to safariJS(jsProbePhase()) as text
		if phaseRetry is "bad_creds" then return "fail:bad_creds"
		return "fail:still-login-form"
	end if
	return "fail:no-account-after-login"
end signInWithCredentials

on jsClickLogoutEverywhere()
	-- Exact Riot control:
	-- <button type="submit" data-testid="log-out-everywhere-button" title="LOG OUT EVERYWHERE">
	-- Prefer form.requestSubmit so React/SSO logout handlers actually fire.
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
		"  var form = btn.form || btn.closest('form');" & ¬
		"  try {" & ¬
		"    if (form && typeof form.requestSubmit === 'function') form.requestSubmit(btn);" & ¬
		"    else btn.click();" & ¬
		"  } catch (e3) { btn.click(); }" & ¬
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
		waitTick(0.4)
	end repeat
	return "timeout"
end waitForLogoutConfirmButton

on jsConfirmLogoutEverywhere()
	-- Prefer exact Riot Confirm control: data-testid=modal_close-btn title=Confirm
	return "(function () {" & ¬
		"  function activate(el) {" & ¬
		"    var form = el.form || el.closest('form');" & ¬
		"    try {" & ¬
		"      if (form && typeof form.requestSubmit === 'function') form.requestSubmit(el);" & ¬
		"      else el.click();" & ¬
		"    } catch (e0) { el.click(); }" & ¬
		"  }" & ¬
		"  var btn = document.querySelector('button[data-testid=modal_close-btn]');" & ¬
		"  if (btn && !btn.disabled) {" & ¬
		"    var label = ((btn.getAttribute('title') || '') + ' ' + (btn.textContent || '')).trim();" & ¬
		"    activate(btn);" & ¬
		"    return 'confirmed-logout:modal_close-btn:' + label.slice(0, 40);" & ¬
		"  }" & ¬
		"  var roots = Array.from(document.querySelectorAll('[role=dialog], .modal, .ds-modal, [class*=modal]'));" & ¬
		"  var scope = roots.length ? roots[roots.length - 1] : document;" & ¬
		"  var btns = Array.from(scope.querySelectorAll('button[type=submit], button, input[type=submit]'));" & ¬
		"  for (var i = 0; i < btns.length; i++) {" & ¬
		"    var el = btns[i];" & ¬
		"    var txt = ((el.textContent || el.value || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.getAttribute('data-testid') || '')).toLowerCase();" & ¬
		"    if ((txt.indexOf('confirm') >= 0 || txt.indexOf('log out everywhere') >= 0) && !el.disabled) {" & ¬
		"      activate(el);" & ¬
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
		waitTick(0.5)
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
		waitTick(0.5)
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
		"    riotbarLogout: has('a[data-testid=" & quote & "riotbar:account:link-logout" & quote & "]')," & ¬
		"    captcha: has('iframe[src*=hcaptcha.com]')," & ¬
		"    loginUser: has('input[name=username], input[autocomplete=username]')," & ¬
		"    loginPass: has('input[name=password], input[type=password]')," & ¬
		"    mfa: has('input[name=code], input[autocomplete=one-time-code], input[inputmode=numeric]')," & ¬
		"    bodySnippet: snippet" & ¬
		"  });" & ¬
		"})();"
end jsDumpPageState

on humanDelay(minSec, maxSec)
	-- Jittered pause so pacing is less robotic. Slice so Stop can land.
	set lo to minSec as real
	set hi to maxSec as real
	if hi < lo then set hi to lo
	set span to hi - lo
	set remaining to lo + (span * (random number from 0 to 1000) / 1000.0)
	repeat while remaining > 0
		assertNotStopped()
		set slice to remaining
		if slice > 0.25 then set slice to 0.25
		delay slice
		set remaining to remaining - slice
	end repeat
end humanDelay

on warmupSafari()
	-- Prime a normal Safari document before Riot (reduces cold-start CF hits).
	logLine("Warming up Safari with a normal page…")
	ensureSafariDocument()
	with timeout of 45 seconds
		tell application "Safari"
			try
				set URL of document 1 to "https://www.apple.com/"
			on error
				make new document with properties {URL:"https://www.apple.com/"}
			end try
		end tell
	end timeout
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
		waitTick(0.5)
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
		waitTick(0.6)
	end repeat
	return "timeout"
end waitForLoginForm

on waitForRiotLogin(timeoutSec)
	-- Match location.hostname only (ignore hosts embedded in query strings).
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
					error "Cloudflare challenge while opening account.riotgames.com."
				end if
			end if
		end try
		waitTick(0.6)
	end repeat
	error "Timed out waiting for Riot host after opening entry (still not on *.riotgames.com)."
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
		waitTick(1.2)
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
		waitTick(0.35)
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
				safariGoTo("https://account.riotgames.com/")
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
			safariGoTo("https://account.riotgames.com/")
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
		safariGoTo("https://account.riotgames.com/")
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
		waitTick(0.35)
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
		return "BUILD 2026-08-12f" & return & return & "Found your CSV at:" & return & found & return & return & "In Terminal run:" & return & "cd " & quoted form of rootDir & return & "./run_safari_mac.sh" & return & return & "Or double-click RUN_ME.command in that folder." & return & return & "(Do not use an older Desktop/riotemail copy of the scripts.)"
	end if
	return "BUILD 2026-08-12f" & return & return & "Put accounts in data/tasks.csv inside your Desktop toolkit folder, then run RUN_ME.command or ./run_safari_mac.sh"
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

	display dialog "BUILD 2026-08-12f" & return & return & "Found " & rowCount & " account(s) in:" & return & csvPath & return & return & "Scripts:" & return & dir & return & return & "Run all now via Safari?" & return & return & "To hard-stop later: double-click STOP_BATCH.command" buttons {"Cancel", "Run all"} default button "Run all"

	logLine("Launching batch for " & rowCount & " account(s) from " & csvPath)
	logLine("Using toolkit scripts: " & dir)
	-- Launch via shell helper (avoid backslash-quote inside AppleScript string literals).
	try
		do shell script "chmod +x " & quoted form of (dir & "/launch_safari_batch.sh") & " " & quoted form of (dir & "/stop_safari_batch.sh") & " " & quoted form of (dir & "/STOP_BATCH.command")
	end try
	try
		do shell script "/bin/bash " & quoted form of (dir & "/launch_safari_batch.sh") & " " & quoted form of csvPath
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
	-- perl alarm hard-caps hung IMAP (AppleScript with timeout does not cover do shell script).
	try
		assertNotStopped()
		set dir to scriptDir()
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
		set cmd to "cd " & quoted form of dir & " && TOOL_DIR=" & quoted form of dir & " perl -e 'alarm shift; exec @ARGV' 110 " & quoted form of py & " " & quoted form of (dir & "/fetch_riot_imap_code.py") & " --prefix " & quoted form of prefix & " --timeout 90 --since-seconds 240"
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
	-- perl alarm slightly above Python --timeout so a hung socket cannot pin osascript forever.
	try
		assertNotStopped()
		set dir to scriptDir()
		set py to do shell script "if [ -x " & quoted form of (dir & "/.venv/bin/python") & " ]; then echo " & quoted form of (dir & "/.venv/bin/python") & "; else command -v python3; fi"
		set helperPath to dir & "/fetch_riot_verify_link.py"
		set cmd to "cd " & quoted form of dir & " && TOOL_DIR=" & quoted form of dir & " NEW_EMAIL=" & quoted form of recipientEmail & " perl -e 'alarm shift; exec @ARGV' 260 " & quoted form of py & " " & quoted form of helperPath & " --prefix " & quoted form of prefix & " --timeout 240 --since-epoch " & quoted form of sinceEpoch & " --subject " & quoted form of "Verify Your Email" & " --recipient " & quoted form of recipientEmail
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

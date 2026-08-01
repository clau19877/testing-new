#!/usr/bin/osascript
(*
  Premium Bandai USA signup helper (personal use)
  - Safari UI flow with human-like pacing (Shape-friendly)
  - iCloud IMAP auth-code retrieval via fetch_icloud_code.py
  - GrizzlySMS phone + SMS OTP (service bvq = PREMIUM BANDAI)
*)

property toolDir : ""
property configPath : ""
property taskPath : ""
property humanizeOn : true
property currentEmail : ""
property currentPassword : ""
property currentTaskJSON : ""
property currentPhone : ""
property currentActivationId : ""

-- cached config values (loaded once per run to avoid repeated subprocess calls)
property cfgWarmupBrowse : true
property cfgGrizzlyEnabled : true
property cfgAskConfirmSuccess : true
property cfgCooldownMinSec : 8
property cfgCooldownMaxSec : 20
property cfgMinActionMs : 450
property cfgMaxActionMs : 1600
property cfgMinKeyMs : 55
property cfgMaxKeyMs : 180
property cfgThinkChance : 0.18
property cfgThinkMinMs : 800
property cfgThinkMaxMs : 2600
property cfgTypoChance : 0.04
property cfgScrollChance : 0.35
property cfgMouseWiggle : true
property cfgSmsWaitTries : 25
property cfgSmsWaitIntervalMs : 500

on run
	set toolDir to do shell script "cd \"$(dirname " & quoted form of (POSIX path of (path to me)) & ")\" && pwd"
	set configPath to toolDir & "/config.json"
	set taskPath to toolDir & "/task.csv"
	if (do shell script "test -f " & quoted form of configPath & " && echo yes || echo no") is "no" then
		error "Missing config.json. Copy config.example.json to config.json and fill it in."
	end if
	if (do shell script "test -f " & quoted form of taskPath & " && echo yes || echo no") is "no" then
		error "Missing task.csv. Copy task.example.csv to task.csv and add email,password rows."
	end if
	set humanizeOn to my cfgBool("humanize.enabled", true)
	set cfgWarmupBrowse to my cfgBool("humanize.warmup_browse", true)
	set cfgGrizzlyEnabled to my cfgBool("grizzly.enabled", true)
	set cfgAskConfirmSuccess to my cfgBool("queue.ask_confirm_success", true)
	set cfgCooldownMinSec to my cfgInt("queue.cooldown_min_sec", 8)
	set cfgCooldownMaxSec to my cfgInt("queue.cooldown_max_sec", 20)
	set cfgMinActionMs to my cfgInt("humanize.min_action_delay_ms", 450)
	set cfgMaxActionMs to my cfgInt("humanize.max_action_delay_ms", 1600)
	set cfgMinKeyMs to my cfgInt("humanize.min_key_delay_ms", 55)
	set cfgMaxKeyMs to my cfgInt("humanize.max_key_delay_ms", 180)
	set cfgThinkChance to my cfgNum("humanize.think_pause_chance", 0.18)
	set cfgThinkMinMs to my cfgInt("humanize.think_pause_min_ms", 800)
	set cfgThinkMaxMs to my cfgInt("humanize.think_pause_max_ms", 2600)
	set cfgTypoChance to my cfgNum("humanize.typo_chance", 0.04)
	set cfgScrollChance to my cfgNum("humanize.scroll_chance", 0.35)
	set cfgMouseWiggle to my cfgBool("humanize.mouse_wiggle", true)
	set cfgSmsWaitTries to my cfgInt("grizzly.sms_screen_wait_tries", 25)
	set cfgSmsWaitIntervalMs to my cfgInt("grizzly.sms_screen_wait_interval_ms", 500)
	
	do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " init"
	my logInfo("Queue run started", "queue_start")
	
	set processed to 0
	set succeeded to 0
	set failedCount to 0
	
	repeat
		set taskCount to (do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " count") as integer
		if taskCount <= 0 then exit repeat
		
		try
			set currentTaskJSON to do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " next"
			if currentTaskJSON is "{}" then exit repeat
			if currentTaskJSON contains "\"error\"" then error "task.csv next failed: " & currentTaskJSON
			
			set currentEmail to my taskStr("email")
			set currentPassword to my taskStr("password")
			set currentPhone to ""
			set currentActivationId to ""
			if (currentEmail is "") or (currentPassword is "") then error "task.csv row missing email/password"
			my logInfo("Starting task", "task_start")
			
			my processOneTask()
			my markSuccess(currentEmail, "created")
			my logInfo("Task succeeded", "task_success")
			set succeeded to succeeded + 1
		on error errMsg
			if not (errMsg starts with "[step:") then
				my logError(errMsg, "task_failed")
			end if
			my cancelGrizzlyIfNeeded()
			try
				my markFailed(currentEmail, currentPassword, errMsg)
			on error markErr
				my logError("markFailed also failed: " & markErr, "mark_failed")
			end try
			set failedCount to failedCount + 1
		end try
		
		set processed to processed + 1
		-- Cool-down between accounts (Shape-sensitive).
		set leftCount to (do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " count") as integer
		if leftCount > 0 then
			my humanPause("cooldown between tasks")
			delay (my randBetween(cfgCooldownMinSec, cfgCooldownMaxSec))
		end if
	end repeat
	
	my logInfo("Queue finished processed=" & processed & " success=" & succeeded & " failed=" & failedCount, "queue_end")
	display notification "Done. success=" & succeeded & " failed=" & failedCount with title "Premium Bandai Signup"
	display dialog "Queue finished." & return & return & "Processed: " & processed & return & "Success: " & succeeded & " → success.csv" & return & "Failed: " & failedCount & " → failed.csv" & return & "Errors: logs/errors.jsonl" & return & "Remaining in task.csv: " & (do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " count") buttons {"OK"} default button 1
end run

on processOneTask()
	try
		my ensureSafariFront()
	on error errMsg
		my failStep("safari_ready", errMsg)
	end try
	
	if cfgWarmupBrowse then
		try
			my humanWarmup()
		on error errMsg
			my failStep("warmup_browse", errMsg)
		end try
	end if
	
	try
		my openRegisterPage()
	on error errMsg
		my failStep("open_register", errMsg)
	end try
	my humanPause("reading age gate")
	
	try
		my clickAgeOver18()
	on error errMsg
		my failStep("age_gate", errMsg)
	end try
	my humanPause("looking at signup options")
	
	try
		my focusBySelectors({"input[type='email']", "input[name*='mail' i]", "input[id*='mail' i]"})
		my humanType(currentEmail)
	on error errMsg
		my failStep("email_entry", errMsg)
	end try
	my humanPause("checking email")
	
	set submitEpoch to do shell script "date +%s"
	try
		my clickButtonNamed({"SUBMIT", "Submit", "Continue", "NEXT", "Next"})
		my waitForAuthCodeScreen()
	on error errMsg
		my failStep("email_submit", errMsg)
	end try
	my humanPause("waiting for inbox")
	
	set authCode to ""
	try
		set authCode to my fetchICloudCode(submitEpoch, currentEmail)
	on error errMsg
		my failStep("fetch_icloud_code", errMsg)
	end try
	my humanPause("reading code email")
	
	try
		my focusBySelectors({"input[name*='code' i]", "input[id*='code' i]", "input[autocomplete='one-time-code']", "input[type='tel']", "input[type='text']"})
		my humanType(authCode)
		my humanPause("confirming code")
		my clickButtonNamed({"SUBMIT", "Submit", "Continue", "Verify", "Authenticate"})
	on error errMsg
		my failStep("email_code_submit", errMsg)
	end try
	
	try
		my waitForEnterInformation()
	on error errMsg
		my failStep("wait_enter_information", errMsg)
	end try
	
	if cfgGrizzlyEnabled then
		try
			my rentGrizzlyNumber()
		on error errMsg
			my failStep("grizzly_rent", errMsg)
		end try
	else
		set currentPhone to my taskStr("phone")
	end if
	
	try
		my fillProfileIfPresent()
	on error errMsg
		my failStep("fill_profile", errMsg)
	end try
	
	-- Optional auto-continue through confirmation if buttons exist.
	try
		my clickButtonNamed({"CONTINUE", "Continue", "CONFIRM", "Confirm", "REGISTER", "Register", "SUBMIT", "Submit"})
		my humanPause("waiting for completion")
	on error contErr
		my logInfo("Continue/confirm button not clicked: " & contErr, "profile_continue_optional")
	end try
	
	-- Phone/SMS verification step (GrizzlySMS)
	if cfgGrizzlyEnabled and (currentActivationId is not "") then
		if my waitForSmsScreen(cfgSmsWaitTries) then
			my humanPause("waiting for SMS")
			set smsCode to ""
			try
				set smsCode to my waitGrizzlySmsCode()
			on error errMsg
				my failStep("grizzly_wait_sms", errMsg)
			end try
			try
				my focusBySelectors({"input[name*='code' i]", "input[id*='code' i]", "input[autocomplete='one-time-code']", "input[type='tel']", "input[type='text']"})
				my humanType(smsCode)
				my humanPause("confirming SMS")
				my clickButtonNamed({"SUBMIT", "Submit", "VERIFY", "Verify", "Authenticate", "Continue", "CONFIRM", "Confirm"})
			on error errMsg
				my failStep("sms_code_submit", errMsg)
			end try
			my humanPause("after SMS verify")
		else
			my logInfo("No SMS screen detected after profile continue", "sms_screen_missing")
		end if
	end if
	
	if cfgAskConfirmSuccess then
		set phoneLine to ""
		if currentPhone is not "" then set phoneLine to return & "Phone: " & currentPhone
		set answer to button returned of (display dialog "Account for:" & return & currentEmail & phoneLine & return & return & "Mark this row as SUCCESS and remove it from task.csv?" buttons {"Mark Failed", "Mark Success"} default button "Mark Success")
		if answer is "Mark Failed" then
			my failStep("user_confirm", "Marked failed by user")
		end if
	else
		if not my pageLooksSuccessful() then
			my failStep("auto_confirm", "Could not confirm success page automatically")
		end if
	end if
end processOneTask

on failStep(stepName, errMsg)
	my logError(errMsg, stepName)
	error "[step:" & stepName & "] " & errMsg
end failStep

on logError(messageText, stepName)
	set safeMsg to my replaceText(messageText, return, " ")
	set safeMsg to my replaceText(safeMsg, linefeed, " ")
	try
		do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/signup_log.py") & " write --level ERROR --source applescript --step " & quoted form of stepName & " --email " & quoted form of currentEmail & " --phone " & quoted form of currentPhone & " --activation-id " & quoted form of currentActivationId & " --message " & quoted form of safeMsg
	end try
end logError

on logInfo(messageText, stepName)
	set safeMsg to my replaceText(messageText, return, " ")
	set safeMsg to my replaceText(safeMsg, linefeed, " ")
	try
		do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/signup_log.py") & " write --level INFO --source applescript --step " & quoted form of stepName & " --email " & quoted form of currentEmail & " --phone " & quoted form of currentPhone & " --activation-id " & quoted form of currentActivationId & " --message " & quoted form of safeMsg
	end try
end logInfo

on pageLooksSuccessful()
	try
		set r to my safariJS("(function(){ const t=(document.body&&document.body.innerText)||''; return /COMPLETE|registration (is )?complete|successfully|welcome/i.test(t) ? 'yes':'no'; })()")
		return r is "yes"
	on error
		return false
	end try
end pageLooksSuccessful

on markSuccess(emailAddr, noteText)
	do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " success --email " & quoted form of emailAddr & " --note " & quoted form of noteText & " --phone " & quoted form of currentPhone & " --activation-id " & quoted form of currentActivationId
end markSuccess

on markFailed(emailAddr, passText, reasonText)
	set safeReason to my replaceText(reasonText, return, " ")
	set safeReason to my replaceText(safeReason, linefeed, " ")
	try
		do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/csv_queue.py") & " --dir " & quoted form of toolDir & " failed --email " & quoted form of emailAddr & " --password " & quoted form of passText & " --reason " & quoted form of safeReason & " --phone " & quoted form of currentPhone & " --activation-id " & quoted form of currentActivationId
	end try
end markFailed

on rentGrizzlyNumber()
	set raw to do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/grizzly_sms.py") & " --config " & quoted form of configPath & " rent"
	set currentActivationId to my jsonField(raw, "activation_id")
	set fullPhone to my jsonField(raw, "phone")
	set formPhone to my jsonField(raw, "phone_form")
	if formPhone is "" then set formPhone to fullPhone
	set currentPhone to formPhone
	if (currentActivationId is "") or (currentPhone is "") then error "GrizzlySMS rent returned empty phone/activation id: " & raw
	my humanPause("got virtual number")
end rentGrizzlyNumber

on waitGrizzlySmsCode()
	if currentActivationId is "" then error "No GrizzlySMS activation id"
	return do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/grizzly_sms.py") & " --config " & quoted form of configPath & " wait --id " & quoted form of currentActivationId
end waitGrizzlySmsCode

on cancelGrizzlyIfNeeded()
	if currentActivationId is "" then return
	try
		do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/grizzly_sms.py") & " --config " & quoted form of configPath & " cancel --id " & quoted form of currentActivationId
	end try
end cancelGrizzlyIfNeeded

on waitForSmsScreen(maxTries)
	repeat maxTries times
		try
			set r to my safariJS("(function(){ const t=(document.body&&document.body.innerText)||''; return /SMS|phone verification|authentication code|verification code|confirm code|one-time|OTP/i.test(t) ? 'yes':'no'; })()")
			if r is "yes" then return true
		end try
		delay (cfgSmsWaitIntervalMs / 1000)
	end repeat
	return false
end waitForSmsScreen

on jsonField(jsonText, keyName)
	set py to "import json,sys; d=json.loads(sys.argv[1]); print((d.get(sys.argv[2],'') or ''))"
	return do shell script "/usr/bin/python3 -c " & quoted form of py & " " & quoted form of jsonText & " " & quoted form of keyName
end jsonField


(* ===== Config ===== *)

on cfgStr(dottedKey)
	return do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/json_get.py") & " " & quoted form of configPath & " " & quoted form of dottedKey
end cfgStr

on cfgStrDefault(dottedKey, fallback)
	return do shell script "/usr/bin/python3 " & quoted form of (toolDir & "/json_get.py") & " " & quoted form of configPath & " " & quoted form of dottedKey & " --default " & quoted form of fallback
end cfgStrDefault

on cfgBool(dottedKey, fallback)
	set fb to "false"
	if fallback then set fb to "true"
	set v to my cfgStrDefault(dottedKey, fb)
	return v is in {"true", "True", "1", "yes"}
end cfgBool

on cfgInt(dottedKey, fallback)
	try
		return (my cfgStr(dottedKey)) as integer
	on error
		return fallback
	end try
end cfgInt

on cfgNum(dottedKey, fallback)
	try
		return (my cfgStr(dottedKey)) as real
	on error
		return fallback
	end try
end cfgNum

on taskStr(keyName)
	set py to "import json,sys; d=json.loads(sys.argv[1]); print((d.get(sys.argv[2],'') or '').strip())"
	return do shell script "/usr/bin/python3 -c " & quoted form of py & " " & quoted form of currentTaskJSON & " " & quoted form of keyName
end taskStr

(* ===== Humanization ===== *)

on randBetween(lo, hi)
	if hi <= lo then return lo
	return lo + (random number from 0 to (hi - lo))
end randBetween

on sleepMs(ms)
	if ms <= 0 then return
	delay (ms / 1000)
end sleepMs

on humanPause(reason)
	if not humanizeOn then
		delay 0.35
		return
	end if
	my sleepMs(my randBetween(cfgMinActionMs, cfgMaxActionMs))
	if (random number from 0.0 to 1.0) < cfgThinkChance then
		my sleepMs(my randBetween(cfgThinkMinMs, cfgThinkMaxMs))
		if cfgMouseWiggle then my mouseWiggle()
	end if
	if (random number from 0.0 to 1.0) < cfgScrollChance then
		my humanScroll()
	end if
end humanPause

on humanScroll()
	try
		my safariJS("(function(){ const y = 120 + Math.floor(Math.random()*280); window.scrollBy({top: (Math.random()<0.5?-1:1)*y, left:0, behavior:'smooth'}); return 'ok'; })()")
		delay (my randBetween(250, 700) / 1000)
	end try
end humanScroll

on mouseWiggle()
	try
		tell application "System Events"
			key code 125
			delay 0.12
			key code 126
		end tell
	end try
end mouseWiggle

on humanType(theText)
	if theText is "" then return
	tell application "Safari" to activate
	delay 0.2
	tell application "System Events"
		keystroke "a" using command down
		delay 0.08
		key code 51
	end tell
	set typoChance to cfgTypoChance
	set klo to cfgMinKeyMs
	set khi to cfgMaxKeyMs
	set chars to characters of theText
	repeat with i from 1 to count of chars
		set ch to item i of chars as text
		if humanizeOn and ((random number from 0.0 to 1.0) < typoChance) and (i > 1) and (i < (count of chars)) then
			set wrong to item (my randBetween(1, count of chars)) of chars as text
			tell application "System Events" to keystroke wrong
			my sleepMs(my randBetween(klo, khi))
			tell application "System Events" to key code 51
			my sleepMs(my randBetween(klo, khi + 80))
		end if
		tell application "System Events" to keystroke ch
		if humanizeOn then
			my sleepMs(my randBetween(klo, khi))
			if (random number from 0.0 to 1.0) < 0.07 then my sleepMs(my randBetween(220, 500))
		else
			delay 0.03
		end if
	end repeat
end humanType


(* ===== Safari helpers ===== *)

on ensureSafariFront()
	tell application "Safari"
		activate
		if (count of windows) is 0 then make new document
	end tell
	delay 0.6
end ensureSafariFront

on safariJS(jsText)
	tell application "Safari"
		return do JavaScript jsText in document 1
	end tell
end safariJS

on openURL(u)
	tell application "Safari"
		activate
		set URL of document 1 to u
	end tell
	my waitForPageReady()
end openURL

on waitForPageReady()
	repeat 40 times
		try
			set pageState to my safariJS("document.readyState")
			if (pageState is "complete") or (pageState is "interactive") then exit repeat
		end try
		delay 0.35
	end repeat
	my humanPause("page settle")
end waitForPageReady

on humanWarmup()
	my openURL(my cfgStr("pbandai.base_url"))
	my humanPause("browsing storefront")
	my humanScroll()
	delay (my randBetween(900, 1800) / 1000)
	my humanScroll()
	my humanPause("deciding to register")
end humanWarmup

on openRegisterPage()
	my openURL(my cfgStr("pbandai.register_url"))
end openRegisterPage

on clickAgeOver18()
	set js to "(function(){ const nodes=[...document.querySelectorAll('button,a,input[type=button],input[type=submit],div[role=button]')]; const hit=nodes.find(n=>/OVER THE AGE OF 18|Over 18|I am 18/i.test((n.innerText||n.value||'').trim())); if(!hit) return 'missing'; hit.scrollIntoView({block:'center'}); hit.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true})); hit.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); hit.dispatchEvent(new PointerEvent('pointerup',{bubbles:true})); hit.dispatchEvent(new MouseEvent('mouseup',{bubbles:true})); hit.click(); return 'ok'; })()"
	set r to my safariJS(js)
	if r is "ok" then
		delay (my randBetween(500, 1200) / 1000)
		my waitForPageReady()
	end if
end clickAgeOver18

on focusBySelectors(selectorList)
	set arr to "["
	repeat with i from 1 to count of selectorList
		set sel to item i of selectorList
		set arr to arr & "'" & my escapeJS(sel) & "'"
		if i < (count of selectorList) then set arr to arr & ","
	end repeat
	set arr to arr & "]"
	set js to "(function(){ const sels=" & arr & "; for (const s of sels){ const el=document.querySelector(s); if(el && el.offsetParent!==null){ el.scrollIntoView({block:'center'}); el.focus(); el.dispatchEvent(new MouseEvent('click',{bubbles:true})); return 'ok:'+s; } } return 'missing'; })()"
	set r to my safariJS(js)
	if r starts with "missing" then error "Could not focus expected input field. Page layout may have changed."
	delay 0.25
end focusBySelectors

on clickButtonNamed(nameList)
	set arr to "["
	repeat with i from 1 to count of nameList
		set n to item i of nameList
		set arr to arr & "'" & my escapeJS(n) & "'"
		if i < (count of nameList) then set arr to arr & ","
	end repeat
	set arr to arr & "]"
	set js to "(function(){ const names=" & arr & "; const nodes=[...document.querySelectorAll('button,a,input[type=submit],input[type=button],div[role=button]')]; for (const name of names){ const hit=nodes.find(n=>{ const t=((n.innerText||n.value||'').trim()); return t.toUpperCase()===name.toUpperCase() || t.toUpperCase().includes(name.toUpperCase()); }); if(hit){ hit.scrollIntoView({block:'center'}); hit.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true})); hit.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); hit.dispatchEvent(new PointerEvent('pointerup',{bubbles:true})); hit.dispatchEvent(new MouseEvent('mouseup',{bubbles:true})); hit.click(); return 'ok:'+name; } } return 'missing'; })()"
	my humanPause("moving to button")
	set r to my safariJS(js)
	if r starts with "missing" then error "Could not find expected button on page."
	delay (my randBetween(700, 1500) / 1000)
	my waitForPageReady()
end clickButtonNamed

on waitForAuthCodeScreen()
	repeat 45 times
		try
			set r to my safariJS("(function(){ const t=(document.body&&document.body.innerText)||''; return /AUTHENTICATION CODE|authentication code|Resend code|emailaddressauth/i.test(t+' '+location.href) ? 'yes':'no'; })()")
			if r is "yes" then return
		end try
		delay 0.5
	end repeat
	error "Timed out waiting for email authentication code screen."
end waitForAuthCodeScreen

on waitForEnterInformation()
	repeat 60 times
		try
			set r to my safariJS("(function(){ const t=(document.body&&document.body.innerText)||''; return /ENTER INFORMATION|Password|First Name|Date of birth|CONFIRMATION/i.test(t) ? 'yes':'no'; })()")
			if r is "yes" then return
		end try
		delay 0.5
	end repeat
	error "Timed out waiting for ENTER INFORMATION screen."
end waitForEnterInformation

on fillProfileIfPresent()
	-- email/password always come from the current task.csv row
	set passText to currentPassword
	if passText is "" then error "task.csv row missing password"
	set phoneText to currentPhone
	if phoneText is "" then set phoneText to my taskStr("phone")
	my tryFillLabelled("First Name", my taskStr("first_name"))
	my tryFillLabelled("Last Name", my taskStr("last_name"))
	my tryFillLabelled("Password", passText)
	my tryFillLabelled("Confirm Password", passText)
	my tryFillLabelled("Phone", phoneText)
	my tryFillLabelled("Mobile", phoneText)
	my tryFillLabelled("Telephone", phoneText)
	my tryFillLabelled("Zip", my taskStr("zip"))
	my tryFillLabelled("Postal", my taskStr("zip"))
	my tryFillLabelled("Address", my taskStr("address1"))
	my tryFillLabelled("City", my taskStr("city"))
	my tryFillLabelled("State", my taskStr("state"))
	my tryFillLabelled("Month", my taskStr("month"))
	my tryFillLabelled("Day", my taskStr("day"))
	my tryFillLabelled("Year", my taskStr("year"))
	my humanPause("reviewing profile")
	my humanScroll()
end fillProfileIfPresent

on tryFillLabelled(labelText, valueText)
	if valueText is "" then return
	set safeLabel to my escapeJS(labelText)
	set safeValue to my escapeJS(valueText)
	set js to "(function(){ const label='" & safeLabel & "'; const value='" & safeValue & "'; const re=new RegExp(label,'i'); const free=el=>!(el.dataset&&el.dataset.pbandaiFilled==='1'); const labs=[...document.querySelectorAll('label,span,p,div,th,td')]; let input=null; for (const lab of labs){ if(!re.test((lab.textContent||'').trim())) continue; if(lab.control && free(lab.control)){ input=lab.control; break; } const near=lab.parentElement && lab.parentElement.querySelector('input,select,textarea'); if(near && free(near)){ input=near; break; } } if(!input){ input=[...document.querySelectorAll('input,select,textarea')].find(el=>free(el) && (re.test(el.name||'')||re.test(el.id||'')||re.test(el.placeholder||'')||re.test(el.getAttribute('aria-label')||''))); } if(!input) return 'missing'; input.scrollIntoView({block:'center'}); if(input.tagName==='SELECT'){ const opts=[...input.options]; let m=opts.find(o=>(o.value||'').toLowerCase()===value.toLowerCase()||(o.textContent||'').trim().toLowerCase()===value.toLowerCase()); if(!m) m=opts.find(o=>(o.textContent||'').toLowerCase().includes(value.toLowerCase())||(o.value||'').toLowerCase().includes(value.toLowerCase())); if(!m) return 'missing'; input.value=m.value; input.dispatchEvent(new Event('input',{bubbles:true})); input.dispatchEvent(new Event('change',{bubbles:true})); input.dataset.pbandaiFilled='1'; return 'select-ok'; } input.dataset.pbandaiFilled='1'; input.focus(); input.click(); return 'ok'; })()"
	try
		set r to my safariJS(js)
		if r is "select-ok" then
			my humanPause("selected " & labelText)
		else if r is "ok" then
			my humanPause("focus " & labelText)
			my humanType(valueText)
		end if
	end try
end tryFillLabelled

on fetchICloudCode(sinceEpoch, toEmail)
	set py to toolDir & "/fetch_icloud_code.py"
	set cmd to "/usr/bin/python3 " & quoted form of py & " --config " & quoted form of configPath & " --since-epoch " & quoted form of sinceEpoch & " --to-email " & quoted form of toEmail
	try
		return do shell script cmd
	on error errMsg
		error "iCloud IMAP code fetch failed: " & errMsg
	end try
end fetchICloudCode

on escapeJS(s)
	set s to my replaceText(s, "\\", "\\\\")
	set s to my replaceText(s, "'", "\\'")
	return s
end escapeJS

on replaceText(t, findText, replaceWith)
	set AppleScript's text item delimiters to findText
	set parts to text items of t
	set AppleScript's text item delimiters to replaceWith
	set outText to parts as text
	set AppleScript's text item delimiters to ""
	return outText
end replaceText

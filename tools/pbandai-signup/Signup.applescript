#!/usr/bin/osascript
(*
  Premium Bandai USA signup helper (personal use)
  - Safari UI flow with human-like pacing (Shape-friendly)
  - iCloud IMAP auth-code retrieval via fetch_icloud_code.py
*)

property toolDir : ""
property configPath : ""
property humanizeOn : true

on run
	set toolDir to do shell script "cd \"$(dirname " & quoted form of (POSIX path of (path to me)) & ")\" && pwd"
	set configPath to toolDir & "/config.json"
	if (do shell script "test -f " & quoted form of configPath & " && echo yes || echo no") is "no" then
		error "Missing config.json. Copy config.example.json to config.json and fill it in."
	end if
	set humanizeOn to my cfgBool("humanize.enabled", true)
	
	my ensureSafariFront()
	if my cfgBool("humanize.warmup_browse", true) then
		my humanWarmup()
	end if
	
	my openRegisterPage()
	my humanPause("reading age gate")
	my clickAgeOver18()
	my humanPause("looking at signup options")
	
	set signupEmail to my cfgStr("pbandai.signup_email")
	my focusBySelectors({"input[type='email']", "input[name*='mail' i]", "input[id*='mail' i]"})
	my humanType(signupEmail)
	my humanPause("checking email")
	
	set submitEpoch to (do shell script "date +%s") as real
	my clickButtonNamed({"SUBMIT", "Submit", "Continue", "NEXT", "Next"})
	my waitForAuthCodeScreen()
	my humanPause("waiting for inbox")
	
	set authCode to my fetchICloudCode(submitEpoch)
	my humanPause("reading code email")
	my focusBySelectors({"input[name*='code' i]", "input[id*='code' i]", "input[autocomplete='one-time-code']", "input[type='tel']", "input[type='text']"})
	my humanType(authCode)
	my humanPause("confirming code")
	my clickButtonNamed({"SUBMIT", "Submit", "Continue", "Verify", "Authenticate"})
	
	my waitForEnterInformation()
	my fillProfileIfPresent()
	
	display notification "Email verified. Review profile, then confirm." with title "Premium Bandai Signup"
	display dialog "iCloud IMAP code accepted." & return & return & "Profile fields were filled when recognized." & return & "Please visually review and click Continue/Confirm yourself — finishing the last submit manually is safer against Shape." buttons {"OK"} default button 1
end run


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


(* ===== Humanization ===== *)

on randBetween(lo, hi)
	if hi ≤ lo then return lo
	return lo + (random number from 0 to (hi - lo))
end randBetween

on sleepMs(ms)
	if ms ≤ 0 then return
	delay (ms / 1000)
end sleepMs

on humanPause(reason)
	if not humanizeOn then
		delay 0.35
		return
	end if
	set lo to my cfgInt("humanize.min_action_delay_ms", 450)
	set hi to my cfgInt("humanize.max_action_delay_ms", 1600)
	my sleepMs(my randBetween(lo, hi))
	if (random number from 0.0 to 1.0) < my cfgNum("humanize.think_pause_chance", 0.18) then
		my sleepMs(my randBetween(my cfgInt("humanize.think_pause_min_ms", 800), my cfgInt("humanize.think_pause_max_ms", 2600)))
		if my cfgBool("humanize.mouse_wiggle", true) then my mouseWiggle()
	end if
	if (random number from 0.0 to 1.0) < my cfgNum("humanize.scroll_chance", 0.35) then
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
	set typoChance to my cfgNum("humanize.typo_chance", 0.04)
	set klo to my cfgInt("humanize.min_key_delay_ms", 55)
	set khi to my cfgInt("humanize.max_key_delay_ms", 180)
	set chars to characters of theText
	repeat with i from 1 to count of chars
		set ch to item i of chars as text
		if humanizeOn and (random number from 0.0 to 1.0) < typoChance and i > 1 and i < (count of chars) then
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
			set st to my safariJS("document.readyState")
			if st is "complete" or st is "interactive" then exit repeat
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
end waitForEnterInformation

on fillProfileIfPresent()
	my tryFillLabelled("First Name", my cfgStr("pbandai.profile.first_name"))
	my tryFillLabelled("Last Name", my cfgStr("pbandai.profile.last_name"))
	my tryFillLabelled("Password", my cfgStr("pbandai.password"))
	my tryFillLabelled("Confirm Password", my cfgStr("pbandai.password"))
	my tryFillLabelled("Phone", my cfgStr("pbandai.profile.phone"))
	my tryFillLabelled("Mobile", my cfgStr("pbandai.profile.phone"))
	my tryFillLabelled("Zip", my cfgStr("pbandai.profile.zip"))
	my tryFillLabelled("Postal", my cfgStr("pbandai.profile.zip"))
	my tryFillLabelled("Address", my cfgStr("pbandai.profile.address1"))
	my tryFillLabelled("City", my cfgStr("pbandai.profile.city"))
	my tryFillLabelled("Month", my cfgStr("pbandai.profile.month"))
	my tryFillLabelled("Day", my cfgStr("pbandai.profile.day"))
	my tryFillLabelled("Year", my cfgStr("pbandai.profile.year"))
	my humanPause("reviewing profile")
	my humanScroll()
end fillProfileIfPresent

on tryFillLabelled(labelText, valueText)
	if valueText is "" then return
	set js to "(function(){ const label='" & my escapeJS(labelText) & "'; const re=new RegExp(label,'i'); const labs=[...document.querySelectorAll('label,span,p,div,th,td')]; let input=null; for (const lab of labs){ if(!re.test((lab.textContent||'').trim())) continue; if(lab.control){ input=lab.control; break;} const near=lab.parentElement && lab.parentElement.querySelector('input,select,textarea'); if(near){ input=near; break;} } if(!input){ input=[...document.querySelectorAll('input,select,textarea')].find(el=>re.test(el.name||'')||re.test(el.id||'')||re.test(el.placeholder||'')||re.test(el.getAttribute('aria-label')||'')); } if(!input) return 'missing'; input.scrollIntoView({block:'center'}); input.focus(); input.click(); return 'ok'; })()"
	try
		set r to my safariJS(js)
		if r is "ok" then
			my humanPause("focus " & labelText)
			my humanType(valueText)
		end if
	end try
end tryFillLabelled

on fetchICloudCode(sinceEpoch)
	set py to toolDir & "/fetch_icloud_code.py"
	set cmd to "/usr/bin/python3 " & quoted form of py & " --config " & quoted form of configPath & " --since-epoch " & sinceEpoch
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

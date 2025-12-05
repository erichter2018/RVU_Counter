;F12::MsgBox, Script is Running!

CoordMode, Mouse, Screen
SetTimer, Monitor_Findings, 2000 ; Start the pixel monitor timer (every 2 seconds)

; --- Color Constants for Pixel Monitoring ---
Findings_Mode_On := 0xFFFFFF
Findings_Mode_Off := 0x3A0000
Out_of_Dictation := 0xF0F1F5
Findings_Reset_State := true ; Start in Reset state
Previous_Color := ""        ; Variable to track the last color seen
; ----------------------------------------

; --- Global flag for Ctrl+F double press state ---
ctrl_f_is_double_press := false
DoubleClickTime_CtrlF := 500 ; Double press window (ms)
SinglePressDelay_CtrlF := 600 ; Delay for single press action (ms), > DoubleClickTime_CtrlF
; --------------------------------------------------

Keys := ["F4", "F5", "F7", "F6"]
No := 0
#IfWinActive ahk_exe InteleViewer.exe 
^!z::Send,% "{" Keys[Mod(No++,Keys.Length())+1] "}"
#if
Return

; --- Ctrl+F Hotkey Logic (Down, Up, Timer) ---
$^f::
    ; ToolTip, Ctrl+F Down Detected`nPrior: %A_PriorHotkey%`nTimeSince: %A_TimeSincePriorHotkey% ; Removed Debug
    ; SetTimer, RemoveToolTip, -2000 ; Removed Debug

    ; Check if prior hotkey was the Down OR Up event for Ctrl+F, and time is short
    if ((A_PriorHotkey = "$^f" or A_PriorHotkey = "$^f Up") and A_TimeSincePriorHotkey < DoubleClickTime_CtrlF)
    {
        ; Potential double press - set flag, cancel single-press timer
        ctrl_f_is_double_press := true
        ; ToolTip, Double Press Flag Set (Waiting for Up) ; Removed Debug
        ; SetTimer, RemoveToolTip, -2000 ; Removed Debug
        SetTimer, DoCtrlFSingle, Off ; Ensure pending single action is cancelled
    }
    else
    {
        ; First press (or too slow) - start single-press timer (only if flag isn't already set)
        if (!ctrl_f_is_double_press)
        {
             ; ToolTip, Single Press Timer Started ; Removed Debug
             ; SetTimer, RemoveToolTip, -2000 ; Removed Debug
             SetTimer, DoCtrlFSingle, % -1 * SinglePressDelay_CtrlF ; Start/Restart single press timer
        }
    }
Return

; Fires when F is RELEASED while Ctrl is held
$^f Up::
    if (ctrl_f_is_double_press)
    {
        ; Flag was set by the down hotkey, this is the confirmation.
        ; Perform double action NOW.
        ; ToolTip, Double Press Action Triggered (On Up) ; Removed Debug
        ; SetTimer, RemoveToolTip, -2000 ; Removed Debug

        MouseGetPos, xpos_dp, ypos_dp
        Click, 5050, 200, 1
        MouseMove, xpos_dp, ypos_dp, 0

        ctrl_f_is_double_press := false ; Reset flag immediately after action
    }
    ; If flag is false, the single press timer is handling it (or already did/will do)
Return

; Timer subroutine for single press action
DoCtrlFSingle:
    ; Check flag just in case it got set between timer start and fire (unlikely but safe)
    if (!ctrl_f_is_double_press)
    {
         ; ToolTip, Single Press Action Triggered (Timer Fired) ; Removed Debug
         ; SetTimer, RemoveToolTip, -2000 ; Removed Debug

         MouseGetPos, xpos_sp, ypos_sp
         Click, 5050, 220, 1
         Click, 5050, 232, 1
         MouseMove, xpos_sp, ypos_sp, 0
    }
    ; else: Double press flag got set, so don't perform single action

    ctrl_f_is_double_press := false ; Ensure flag is reset if timer fires
Return
; -------------------------------------------

^!h::
MouseGetPos, xpos, ypos
Click, 5398, 530, 1
MouseMove, xpos, ypos, 0
Return

KeysS := ["Right", "Left", "Right", "Left"]
NoS := 0
#IfWinActive ahk_exe InteleViewer.exe 
^!a::Send,% "{" KeysS[Mod(NoS++,KeysS.Length())+1] "}"
#if
Return

; --- Pixel Monitoring Routine ---
Monitor_Findings:
    global Findings_Mode_On, Findings_Mode_Off, Out_of_Dictation, Findings_Reset_State, Previous_Color

    ; SoundBeep, 1000, 20 ; Short beep to confirm execution (Removed)
    CoordMode, Pixel, Screen ; Explicitly set coordinate mode for pixel check
    ;PixelGetColor, current_color, 5031, 229, RGB ; Use RGB mode
    PixelGetColor, current_color, 5031, 232, RGB ; Use RGB mode
    CoordMode, ToolTip, Screen ; Explicitly set coordinate mode for tooltip

    ; Tooltip 1: Display current raw color (Removed)
    ;ToolTip, % "RGB Color at 5031,232: " . current_color, 4995, 136

    ; --- Logic for State and Second Tooltip ---
    state_message := "" ; Default empty message

    ; Check if we briefly entered Out_of_Dictation
    if (current_color = Out_of_Dictation)
    {
        Findings_Reset_State := true
    }
    ; Check if color changed from ON to OFF
    else if (Previous_Color = Findings_Mode_On and current_color = Findings_Mode_Off)
    {
        Findings_Reset_State := false
    }

    ; Determine message for second tooltip based on Reset state and current color
    if (Findings_Reset_State)
    {
        if (current_color = Findings_Mode_On)
        {
            state_message := "All Good"
        }
        else if (current_color = Findings_Mode_Off)
        {
            state_message := "NO GOOD"
            Gosub, DoCtrlFSingle ; Trigger the single Ctrl+F action
        }
        ; else, if it's another color while reset is true, show nothing or a specific message?
        ; For now, it will show nothing if not ON or OFF while Reset is true.
    }
    ; else: Reset state is false, don't display All Good/NO GOOD

    ; Tooltip 2: Display state message (Now at the original Y=136 position) - COMMENTED OUT
    ; ToolTip, % state_message, 4995, 136

    ; Update previous color for the next check
    Previous_Color := current_color
Return
; ------------------------------

; =============================================================================
; GET PRIOR - Extract and format prior study information from Mosaic
; Triggered by: Alt+Shift+F3
; =============================================================================
!+F3::
GetPrior:
SetTitleMatchMode, 2

MouseGetPos, , , window, control
WinActivate, %window%
backup := Clipboard				;copy the clipboard to a temp variable
Clipboard := ""
Send ^c 					;copy selected text
ClipWait 0.5, 1

PriorOriginal := Clipboard
PriorDate := ""
PriorComplete := ""
PriorDescript1 := ""
PriorDescript := ""
PriorTime := ""
PriorImages := ""
PriorReport := ""
ModalitySearch := ""

PhraseSearch := "i)(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).*19[0-9][0-9]"
FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDate)

PhraseSearch := "i)(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).*20[0-9][0-9]"
FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDate)

if InStr(PriorOriginal, " PDT")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+PDT)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}

if InStr(PriorOriginal, " PST")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+PST)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}

if InStr(PriorOriginal, " CDT")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+CDT)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}
	
if InStr(PriorOriginal, " CST")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+CST)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}

if InStr(PriorOriginal, " MDT")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+MDT)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}
		
if InStr(PriorOriginal, " MST")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+MST)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)
	
	Gosub, DateFill
}


if InStr(PriorOriginal, " EDT")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+EDT)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}

if InStr(PriorOriginal, " EST")
{
	PhraseSearch := "i)(\d{1,2}:\d{2}:\d{2}\s+EST)"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorTime)

	Gosub, DateFill
}


StringCaseSense, ON

ModalitySearch := A_Tab . "IN_PROGRESS"
if InStr(PriorOriginal, ModalitySearch)
{
	PriorReport := ""
	SearchText := ModalitySearch
	ReplaceText := " SIGNXED"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)
}

ModalitySearch := A_Tab . "NO_HL7_ORDER"
if InStr(PriorOriginal, ModalitySearch)
{
	PriorReport := "No Prior Report. "
	SearchText := ModalitySearch
	ReplaceText := " SIGNXED"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

}

ModalitySearch := A_Tab . "UNKNOWN"
if InStr(PriorOriginal, ModalitySearch)
{
	PriorReport := "No Prior Report. "
	SearchText := ModalitySearch
	ReplaceText := " SIGNXED"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)
}

ModalitySearch := A_Tab . "NO_IMAGES"
if InStr(PriorOriginal, ModalitySearch)
{
	PriorImages := "No Prior Images. "
}

ModalitySearch := A_Tab . "SIGNED"
if InStr(PriorOriginal, ModalitySearch)
{
	SearchText := ModalitySearch
	ReplaceText := " SIGNXED"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)
}

StringCaseSense, Off

ModalitySearch := A_Tab . "US"
if InStr(PriorOriginal, ModalitySearch)
{
	SearchText := "U)US.*US"
	ReplaceText := "US"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	PhraseSearch := "i)US.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "US" from beginning
	SearchText := "US"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText,,1)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, Off

	SearchText := " abd."
	ReplaceText := " abdomen."
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, ON
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T
	
	; Reorder: Insert "Ultrasound" before modifiers if present
	ModifierFound := false
	
	if InStr(PriorDescript1, " With And Without")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, "(\s+)(With And Without)", " Ultrasound$2")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " Without")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, "(\s+)(Without)", " Ultrasound$2")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " With")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, "(\s+)(With)", " Ultrasound$2")
		ModifierFound := true
	}
	
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " Ultrasound"
	}

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "MR"
if InStr(PriorOriginal, ModalitySearch)
{
	SearchText := "U)MR.*MR"
	ReplaceText := "MR"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	PhraseSearch := "i)MR.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "MR" from beginning
	SearchText := "MR"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText,,1)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, Off

	SearchText := " + "
	ReplaceText := " and "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W/O"
	ReplaceText := " without"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W/"
	ReplaceText := " with"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W WO"
	ReplaceText := " with and without"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " WO"
	ReplaceText := " without"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " IV "
	ReplaceText := " "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, ON
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T

	SearchText := "Mr "
	ReplaceText := "MR "
	PriorDescript1 := StrReplace(PriorDescript1, SearchText, ReplaceText)
	
	; Reorder: Insert "MR" before study type and contrast modifiers
	ModifierFound := false
	
	; First check for study type modifiers (angiography, venography)
	if InStr(PriorDescript1, " Angiography")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " Angiography", " MR Angiography")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " Venography")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " Venography", " MR Venography")
		ModifierFound := true
	}
	; Then check for contrast modifiers (with proper spacing)
	else if InStr(PriorDescript1, " With And Without")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (With And Without)", " MR $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " Without")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (Without)", " MR $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " With")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (With)", " MR $1")
		ModifierFound := true
	}
	
	; If no modifier found, add MR at the end
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " MR"
	}

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "NM"
if InStr(PriorOriginal, ModalitySearch)
{

	SearchText := "U)NM.*NM"
	ReplaceText := "NM"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	PhraseSearch := "i)NM.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	SearchText := "NM"
	ReplaceText := "Nuclear Medicine"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringUpper PriorDescript1, PriorDescript, T

	PriorComplete := PriorDate . " " . PriorDescript1 . ". " . PriorReport . PriorImages

	;MsgBox, %PriorDescript1%

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "XR"
if InStr(PriorOriginal, ModalitySearch)
{

	SearchText := "U)CR.*XR"
	ReplaceText := "XR"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	PhraseSearch := "i)XR.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "XR" from beginning
	SearchText := "XR"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText,,1)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T
	
	; Reorder: Insert "Radiograph" before view modifiers if present
	; Handle patterns like "Chest 1 View" -> "Chest Radiograph 1 View"
	; Also handle "3 Or More Radiograph Views" -> "Radiograph 3 Or More Views"
	ModifierFound := false
	
	; First, check if "Radiograph" is already present but in wrong position
	; Pattern: "Body Part 3 Or More Radiograph Views" -> "Body Part Radiograph 3 Or More Views"
	if RegExMatch(PriorDescript1, "i) \d+\s+Or\s+More\s+Radiograph\s+Views?")
	{
		; Move Radiograph before the numeric pattern
		PriorDescript1 := RegExReplace(PriorDescript1, "i) (\d+\s+Or\s+More)\s+Radiograph\s+(Views?)", " Radiograph $1 $2")
		ModifierFound := true
	}
	; Check for numeric view pattern (e.g., "1 View", "2 View")
	else if RegExMatch(PriorDescript1, " \d+\s*View")
	{
		; Insert Radiograph before the number+view pattern
		PriorDescript1 := RegExReplace(PriorDescript1, " (\d+\s*View)", " Radiograph $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " PA And Lateral")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (PA And Lateral)", " Radiograph $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " View")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (View)", " Radiograph $1")
		ModifierFound := true
	}
	
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " Radiograph"
	}

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "XR"
if InStr(PriorOriginal, ModalitySearch)
{

	SearchText := "U)CR.*RAD"
	ReplaceText := "XR"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	PhraseSearch := "i)XR.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "XR" from beginning
	SearchText := "XR"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText,,1)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, Off

	SearchText := " vw"
	ReplaceText := " view(s)"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " 2v"
	ReplaceText := " PA and lateral"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " pa lat"
	ReplaceText := " PA and lateral"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " (kub)"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, ON
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T

	SearchText := " Pa and lateral"
	ReplaceText := " PA and lateral"
	PriorDescript1 := StrReplace(PriorDescript1, SearchText, ReplaceText)
	
	; Reorder: Insert "Radiograph" before view modifiers if present
	; Handle patterns like "Chest 1 View" -> "Chest Radiograph 1 View"
	; Also handle "3 Or More Radiograph Views" -> "Radiograph 3 Or More Views"
	ModifierFound := false
	
	; First, check if "Radiograph" is already present but in wrong position
	; Pattern: "Body Part 3 Or More Radiograph Views" -> "Body Part Radiograph 3 Or More Views"
	if RegExMatch(PriorDescript1, "i) \d+\s+Or\s+More\s+Radiograph\s+Views?")
	{
		; Move Radiograph before the numeric pattern
		PriorDescript1 := RegExReplace(PriorDescript1, "i) (\d+\s+Or\s+More)\s+Radiograph\s+(Views?)", " Radiograph $1 $2")
		ModifierFound := true
	}
	; Check for numeric view pattern (e.g., "1 View", "2 View")
	else if RegExMatch(PriorDescript1, " \d+\s*View")
	{
		; Insert Radiograph before the number+view pattern
		PriorDescript1 := RegExReplace(PriorDescript1, " (\d+\s*View)", " Radiograph $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " PA And Lateral")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (PA And Lateral)", " Radiograph $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " View")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (View)", " Radiograph $1")
		ModifierFound := true
	}
	
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " Radiograph"
	}

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "CR"
if InStr(PriorOriginal, ModalitySearch)
{

	PhraseSearch := "i)CR.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "CR" and tab from beginning
	SearchText := "CR" . A_Tab
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, Off

	SearchText := " vw"
	ReplaceText := " view(s)"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " 2v"
	ReplaceText := " PA and lateral"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " pa lat"
	ReplaceText := " PA and lateral"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " (kub)"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, ON
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T

	SearchText := " Pa and lateral"
	ReplaceText := " PA and lateral"
	PriorDescript1 := StrReplace(PriorDescript1, SearchText, ReplaceText)
	
	; Reorder: Insert "Radiograph" before view modifiers if present
	; Also handle "3 Or More Radiograph Views" -> "Radiograph 3 Or More Views"
	ModifierFound := false
	
	; First, check if "Radiograph" is already present but in wrong position
	; Pattern: "Body Part 3 Or More Radiograph Views" -> "Body Part Radiograph 3 Or More Views"
	if RegExMatch(PriorDescript1, "i) \d+\s+Or\s+More\s+Radiograph\s+Views?")
	{
		; Move Radiograph before the numeric pattern
		PriorDescript1 := RegExReplace(PriorDescript1, "i) (\d+\s+Or\s+More)\s+Radiograph\s+(Views?)", " Radiograph $1 $2")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " PA And Lateral")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, "(\s+)(PA And Lateral)", " Radiograph$2")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " View")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, "(\s+)(View)", " Radiograph$2")
		ModifierFound := true
	}
	
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " Radiograph"
	}

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "X-ray"
if InStr(PriorOriginal, ModalitySearch)
{

	PhraseSearch := "i)X-ray.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "X-ray" variants from beginning
	SearchText := "X-ray, OT" . A_Tab
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "X-ray" . A_Tab
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "Ch-c"
	ReplaceText := "C"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " DR "
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, Off

	SearchText := " vw"
	ReplaceText := " view(s)"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " 2v"
	ReplaceText := " PA and lateral"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " pa lat"
	ReplaceText := " PA and lateral"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " (kub)"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, ON
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T

	SearchText := " Pa and lateral"
	ReplaceText := " PA and lateral"
	PriorDescript1 := StrReplace(PriorDescript1, SearchText, ReplaceText)
	
	; Reorder: Insert "Radiograph" before view modifiers if present
	; Handle patterns like "Chest 1 View" -> "Chest Radiograph 1 View"
	; Also handle "3 Or More Radiograph Views" -> "Radiograph 3 Or More Views"
	ModifierFound := false
	
	; First, check if "Radiograph" is already present but in wrong position
	; Pattern: "Body Part 3 Or More Radiograph Views" -> "Body Part Radiograph 3 Or More Views"
	if RegExMatch(PriorDescript1, "i) \d+\s+Or\s+More\s+Radiograph\s+Views?")
	{
		; Move Radiograph before the numeric pattern
		PriorDescript1 := RegExReplace(PriorDescript1, "i) (\d+\s+Or\s+More)\s+Radiograph\s+(Views?)", " Radiograph $1 $2")
		ModifierFound := true
	}
	; Check for numeric view pattern (e.g., "1 View", "2 View")
	else if RegExMatch(PriorDescript1, " \d+\s*View")
	{
		; Insert Radiograph before the number+view pattern
		PriorDescript1 := RegExReplace(PriorDescript1, " (\d+\s*View)", " Radiograph $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " PA And Lateral")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (PA And Lateral)", " Radiograph $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " View")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (View)", " Radiograph $1")
		ModifierFound := true
	}
	
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " Radiograph"
	}

	Goto, ComparisonFill
}

ModalitySearch := A_Tab . "CT"
if InStr(PriorOriginal, ModalitySearch)
{
	SearchText := "i)Oct"
	ReplaceText := "OcX"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	SearchText := "U)CT.*CT"
	ReplaceText := "CT"
	PriorOriginal := RegExReplace(PriorOriginal, SearchText, ReplaceText)

	PhraseSearch := "i)CT.*SIGNXED"
	FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDescript)

	; Remove "CT" from beginning
	SearchText := "CT"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText,,1)

	SearchText := " SIGNXED"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, Off

	SearchText := " + "
	ReplaceText := " and "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "+"
	ReplaceText := " and "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " imags"
	ReplaceText := ""
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "Head Or Brain"
	ReplaceText := "brain"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W/CONTRST INCL W/O"
	ReplaceText := " with and without contrast"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W/O"
	ReplaceText := " without"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W/ "
	ReplaceText := " with "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W "
	ReplaceText := " with "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W/"
	ReplaceText := " with "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " W WO"
	ReplaceText := " with and without"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " WO"
	ReplaceText := " without"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " IV "
	ReplaceText := " "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "ab pe"
	ReplaceText := "abdomen and pelvis"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "abd/pelvis"
	ReplaceText := "abdomen and pelvis"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := " abd pel "
	ReplaceText := " abdomen and pelvis "
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "abdomen/pelvis"
	ReplaceText := "abdomen and pelvis"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	SearchText := "chest/abdomen/pelvis"
	ReplaceText := "chest, abdomen, and pelvis"
	PriorDescript := StrReplace(PriorDescript, SearchText, ReplaceText)

	StringCaseSense, ON
	
	; Trim whitespace
	PriorDescript := Trim(PriorDescript)

	StringUpper PriorDescript1, PriorDescript, T

	SearchText := "Ct "
	ReplaceText := "CT "
	PriorDescript1 := StrReplace(PriorDescript1, SearchText, ReplaceText)
	
	; Reorder: Insert "CT" before study type and contrast modifiers
	; Desired order: Body Part + CT + Study Type (angiography) + Contrast Modifier
	; Handle "Angiography" as a study type that comes after CT
	
	ModifierFound := false
	
	; First check for "Angiography" - it should come after CT
	if InStr(PriorDescript1, " Angiography")
	{
		; Insert CT before "Angiography"
		PriorDescript1 := RegExReplace(PriorDescript1, " Angiography", " CT Angiography")
		ModifierFound := true
	}
	; Then check for contrast modifiers (with proper spacing)
	else if InStr(PriorDescript1, " With And Without")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (With And Without)", " CT $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " Without")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (Without)", " CT $1")
		ModifierFound := true
	}
	else if InStr(PriorDescript1, " With")
	{
		PriorDescript1 := RegExReplace(PriorDescript1, " (With)", " CT $1")
		ModifierFound := true
	}
	
	; If no modifier found, add CT at the end
	if (!ModifierFound)
	{
		PriorDescript1 := PriorDescript1 . " CT"
	}

	Goto, ComparisonFill

}

ComparisonFill:
; Check if prior study was within the last 2 days - if so, include time
IncludeTime := false
if (PriorDate != "" and PriorTimeFormatted != "")
{
    ; Parse the prior date (format: M/D/YYYY)
    RegExMatch(PriorDate, "i)(\d{1,2})/(\d{1,2})/(\d{4})", DateParts)
    PriorMonth := DateParts1
    PriorDay := DateParts2
    PriorYear := DateParts3
    
    ; Build prior date as YYYYMMDD timestamp for comparison
    if (PriorMonth and PriorDay and PriorYear)
    {
        ; Ensure components are treated as numbers and padded correctly
        PriorMonth += 0  ; Convert to pure number
        PriorDay += 0    ; Convert to pure number
        
        ; Build YYYYMMDD with proper padding
        PriorDateStamp := PriorYear
        PriorDateStamp .= SubStr("0" . PriorMonth, -1)  ; Last 2 chars of "0" + month
        PriorDateStamp .= SubStr("0" . PriorDay, -1)    ; Last 2 chars of "0" + day
        
        ; Get current date as YYYYMMDD
        FormatTime, CurrentDateStamp, , yyyyMMdd
        
        ; Calculate difference using EnvSub with proper format
        DaysDiff := CurrentDateStamp
        EnvSub, DaysDiff, %PriorDateStamp%, Days
        
        ; If within last 2 days (0, 1, or 2), include time
        if (DaysDiff >= 0 and DaysDiff <= 2)
        {
            IncludeTime := true
        }
    }
}

; Build the final comparison text with time if within 36 hours (single space after COMPARISON:)
if (IncludeTime)
{
    FinalText := " COMPARISON: " . PriorDate . " " . PriorTimeFormatted . " " . PriorDescript1 . ". " . PriorReport . PriorImages
}
else
{
    FinalText := " COMPARISON: " . PriorDate . " " . PriorDescript1 . ". " . PriorReport . PriorImages
}

; Activate PowerScribe 360 window
SetTitleMatchMode, 2  ; Partial match
WinActivate, PowerScribe 360

; Wait for window to be active
WinWaitActive, PowerScribe 360, , 1

; Get window ID and ensure focus
pswinID := WinExist("PowerScribe 360")
if (pswinID)
{
	WinActivate, ahk_id %pswinID%
	WinWaitActive, ahk_id %pswinID%, , 1
 
	; Paste the comparison text
	Clipboard := FinalText
	
	Send ^v						;Send Paste
	Sleep, 100					;Pause
}
else
{
	MsgBox, PowerScribe 360 window not found!
}
Return

DateFill:
; Parse the full date string to extract month, day, year, and time
; Expected format: "Dec 03 00:29:00 EST 2025" or similar
; PriorDate contains the full matched string, PriorTime contains just the time portion

; Extract year from PriorDate
RegExMatch(PriorDate, "i)(19[0-9][0-9]|20[0-9][0-9])", YearMatch)
ExtractedYear := YearMatch1

; Extract day (1 or 2 digits followed by space or time)
RegExMatch(PriorDate, "i)([0-9]{1,2})\s+[0-9:]", DayMatch)
ExtractedDay := DayMatch1

; Convert month abbreviations to numbers
MonthNum := ""
if InStr(PriorDate, "Jan")
    MonthNum := "1"
else if InStr(PriorDate, "Feb")
    MonthNum := "2"
else if InStr(PriorDate, "Mar")
    MonthNum := "3"
else if InStr(PriorDate, "Apr")
    MonthNum := "4"
else if InStr(PriorDate, "May")
    MonthNum := "5"
else if InStr(PriorDate, "Jun")
    MonthNum := "6"
else if InStr(PriorDate, "Jul")
    MonthNum := "7"
else if InStr(PriorDate, "Aug")
    MonthNum := "8"
else if InStr(PriorDate, "Sep")
    MonthNum := "9"
else if InStr(PriorDate, "Oct")
    MonthNum := "10"
else if InStr(PriorDate, "Nov")
    MonthNum := "11"
else if InStr(PriorDate, "Dec")
    MonthNum := "12"

; Build formatted date as M/D/YYYY
PriorDate := MonthNum . "/" . ExtractedDay . "/" . ExtractedYear

; Format time with timezone (e.g., "14:30 EST")
if (PriorTime != "")
{
    ; PriorTime contains something like "00:29:00 EST"
    ; Keep the time and timezone, just remove seconds
    PriorTimeFormatted := PriorTime
    ; Remove seconds (e.g., "00:29:00 EST" -> "00:29 EST")
    PriorTimeFormatted := RegExReplace(PriorTimeFormatted, "i)(\d{1,2}:\d{2}):\d{2}", "$1")
}
else
{
    PriorTimeFormatted := ""
}
Return
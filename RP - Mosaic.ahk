;F12::MsgBox, Script is Running!

; --- DEPRECATED KEY COMBINATIONS (removed) ---
; Ctrl+F (low side button) - Used to handle single/double press detection for clicking at specific coordinates
; Ctrl+Alt+H (scroll wheel click) - Used to click at coordinates (5398, 530)
; Findings Mode monitoring - Used to monitor pixel colors and trigger actions based on state changes
; ---------------------------------------------

CoordMode, Mouse, Screen

; --- InteleViewer Window Presets ---
Keys := ["F4", "F5", "F7", "F6"]
No := 0
#IfWinActive ahk_exe InteleViewer.exe 
^!z::Send,% "{" Keys[Mod(No++,Keys.Length())+1] "}"
#if
Return

; --- InteliViewer Scroll Series ---
KeysS := ["Right", "Left", "Right", "Left"]
NoS := 0
#IfWinActive ahk_exe InteleViewer.exe 
^!a::Send,% "{" KeysS[Mod(NoS++,KeysS.Length())+1] "}"
#if
Return

; =============================================================================
; GET PRIOR - Extract and format prior study information from Mosaic
; Triggered by: Alt+Shift+F3
; =============================================================================
!+F3::
GetPrior:
SetTitleMatchMode, 2

; Get window info and copy selected text
MouseGetPos, , , window, control
WinActivate, %window%
ClipboardBackup := Clipboard    ; Backup clipboard
Clipboard := ""
Send ^c                         ; Copy selected text
ClipWait, 0.5, 1

; Validate clipboard has content
if (Clipboard = "")
{
    Clipboard := ClipboardBackup  ; Restore clipboard
    MsgBox, No text selected. Please select a prior study row and try again.
    Return
}

PriorOriginal := Clipboard

; Initialize all variables
PriorDate := ""
PriorDescript := ""
PriorDescript1 := ""
PriorTime := ""
PriorTimeFormatted := ""
PriorImages := ""
PriorReport := ""
ModalitySearch := ""

; =============================================================================
; PARSE DATE AND TIME
; =============================================================================

; Extract date containing month abbreviation and year (supports 1900s and 2000s)
PhraseSearch := "i)(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).*?(19[0-9][0-9]|20[0-9][0-9])"
FoundPos := RegExMatch(PriorOriginal, PhraseSearch, PriorDate)

; Extract time with timezone - single unified pattern for all US timezones
; Covers: EST, EDT, CST, CDT, MST, MDT, PST, PDT, AST, ADT, AKST, AKDT, HST
TimezonePattern := "i)(\d{1,2}:\d{2}:\d{2}\s+(?:E|C|M|P|AK?|H)[SD]T)"
if RegExMatch(PriorOriginal, TimezonePattern, PriorTime)
{
    Gosub, DateFill
}

; =============================================================================
; HANDLE STUDY STATUS FLAGS
; =============================================================================

StringCaseSense, ON

; Handle various status flags - normalize to SIGNXED for processing
    StatusFlags := ["IN_PROGRESS", "NO_HL7_ORDER", "UNKNOWN", "SIGNED"]
    for index, flag in StatusFlags
    {
        ModalitySearch := A_Tab . flag
        if InStr(PriorOriginal, ModalitySearch)
        {
            ; Don't add "No Prior Report" - filter it out
            PriorOriginal := StrReplace(PriorOriginal, ModalitySearch, " SIGNXED")
        }
    }

; Check for NO_IMAGES flag
if InStr(PriorOriginal, A_Tab . "NO_IMAGES")
    PriorImages := "No Prior Images. "

StringCaseSense, Off

; =============================================================================
; MODALITY-SPECIFIC PROCESSING
; =============================================================================

; --- ULTRASOUND (US) ---
ModalitySearch := A_Tab . "US"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Remove duplicate US markers
    PriorOriginal := RegExReplace(PriorOriginal, "U)US.*US", "US")
    
    ; Extract description
    RegExMatch(PriorOriginal, "i)US.*SIGNXED", PriorDescript)
    PriorDescript := StrReplace(PriorDescript, "US", "", , 1)  ; Remove first "US"
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    ; Expand abbreviations
    PriorDescript := StrReplace(PriorDescript, " abd.", " abdomen.")
    
    PriorDescript := Trim(PriorDescript)
    StringLower, PriorDescript1, PriorDescript
    
    ; Reorder laterality before processing ultrasound modifiers
    Gosub, ReorderLaterality
    
    ; Insert "ultrasound" before contrast modifiers or at end
    if InStr(PriorDescript1, " with and without")
        PriorDescript1 := RegExReplace(PriorDescript1, "i)(\s+)(with and without)", " ultrasound$2")
    else if InStr(PriorDescript1, " without")
        PriorDescript1 := RegExReplace(PriorDescript1, "i)(\s+)(without)", " ultrasound$2")
    else if InStr(PriorDescript1, " with")
        PriorDescript1 := RegExReplace(PriorDescript1, "i)(\s+)(with)", " ultrasound$2")
    else
        PriorDescript1 := PriorDescript1 . " ultrasound"
    
    Goto, ComparisonFill
}

; --- MAGNETIC RESONANCE (MR/MRI) ---
ModalitySearch := A_Tab . "MR"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Remove duplicate MR markers
    PriorOriginal := RegExReplace(PriorOriginal, "U)MR.*MR", "MR")
    
    ; Extract description
    RegExMatch(PriorOriginal, "i)MR.*SIGNXED", PriorDescript)
    PriorDescript := StrReplace(PriorDescript, "MR", "", , 1)  ; Remove first "MR"
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    ; Handle MRA/MRV abbreviations (similar to CTA)
    ; Preserve MRA/MRV before removing MR
    HasMRA := InStr(PriorDescript, "MRA")
    HasMRV := InStr(PriorDescript, "MRV")
    
    ; Expand abbreviations
    PriorDescript := StrReplace(PriorDescript, " + ", " and ")
    PriorDescript := StrReplace(PriorDescript, " W/O", " without")
    PriorDescript := StrReplace(PriorDescript, " W/", " with")
    PriorDescript := StrReplace(PriorDescript, " W WO", " with and without")
    PriorDescript := StrReplace(PriorDescript, " WO", " without")
    PriorDescript := StrReplace(PriorDescript, " IV ", " ")
    
    PriorDescript := Trim(PriorDescript)
    StringLower, PriorDescript1, PriorDescript
    
    ; Reorder laterality before processing MR modifiers
    Gosub, ReorderLaterality
    
    ; Reorder: Insert "MR" before study type and contrast modifiers
    ModifierFound := false
    
    ; Handle MRA (angiography) - already in correct form
    if InStr(PriorDescript1, " mra")
    {
        ModifierFound := true
    }
    ; Handle MRV (venography) - already in correct form
    else if InStr(PriorDescript1, " mrv")
    {
        ModifierFound := true
    }
    ; Check for angiography/venography text
    else if InStr(PriorDescript1, " angiography")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(angiography)", " MR $1")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " venography")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(venography)", " MR $1")
        ModifierFound := true
    }
    ; Contrast modifiers
    else if InStr(PriorDescript1, " with and without")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(with and without)", " MR $1")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " without")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(without)", " MR $1")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " with")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(with)", " MR $1")
        ModifierFound := true
    }
    
    if (!ModifierFound)
        PriorDescript1 := PriorDescript1 . " MR"
    
    Goto, ComparisonFill
}

; --- NUCLEAR MEDICINE (NM) ---
ModalitySearch := A_Tab . "NM"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Remove duplicate NM markers
    PriorOriginal := RegExReplace(PriorOriginal, "U)NM.*NM", "NM")
    
    ; Extract and process description
    RegExMatch(PriorOriginal, "i)NM.*SIGNXED", PriorDescript)
    PriorDescript := StrReplace(PriorDescript, "NM", "nuclear medicine")
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    PriorDescript := Trim(PriorDescript)
    StringLower, PriorDescript1, PriorDescript
    
    ; Reorder laterality if present
    Gosub, ReorderLaterality
    
    Goto, ComparisonFill
}

; --- X-RAY / RADIOGRAPH (XR, CR, X-ray) ---
; Handle XR modality
ModalitySearch := A_Tab . "XR"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Remove CR->XR duplicate markers
    PriorOriginal := RegExReplace(PriorOriginal, "U)CR.*XR", "XR")
    
    ; Extract description
    RegExMatch(PriorOriginal, "i)XR.*SIGNXED", PriorDescript)
    PriorDescript := StrReplace(PriorDescript, "XR", "", , 1)
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    Gosub, ProcessRadiograph
    Goto, ComparisonFill
}

; Handle CR modality
ModalitySearch := A_Tab . "CR"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Extract description
    RegExMatch(PriorOriginal, "i)CR.*SIGNXED", PriorDescript)
    PriorDescript := StrReplace(PriorDescript, "CR" . A_Tab, "")
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    Gosub, ProcessRadiograph
    Goto, ComparisonFill
}

; Handle X-ray modality
ModalitySearch := A_Tab . "X-ray"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Extract description
    RegExMatch(PriorOriginal, "i)X-ray.*SIGNXED", PriorDescript)
    PriorDescript := StrReplace(PriorDescript, "X-ray, OT" . A_Tab, "")
    PriorDescript := StrReplace(PriorDescript, "X-ray" . A_Tab, "")
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    ; X-ray specific cleanups
    PriorDescript := StrReplace(PriorDescript, "Ch-c", "C")
    PriorDescript := StrReplace(PriorDescript, " DR ", "")
    
    Gosub, ProcessRadiograph
    Goto, ComparisonFill
}

; --- COMPUTED TOMOGRAPHY (CT) ---
ModalitySearch := A_Tab . "CT"
if InStr(PriorOriginal, ModalitySearch)
{
    ; Protect "October" from CT removal
    PriorOriginal := RegExReplace(PriorOriginal, "i)Oct", "OcX")
    
    ; Remove duplicate CT markers
    PriorOriginal := RegExReplace(PriorOriginal, "U)CT.*CT", "CT")
    
    ; Extract description
    RegExMatch(PriorOriginal, "i)CT.*SIGNXED", PriorDescript)
    
    ; Handle "CTA - " pattern (remove dash)
    PriorDescript := StrReplace(PriorDescript, "CTA - ", "CTA ")
    
    ; Protect CTA before removing CT
    PriorDescript := StrReplace(PriorDescript, "CTA", "CTAPLACEHOLDER")
    
    ; Remove CT from beginning only
    PriorDescript := RegExReplace(PriorDescript, "i)^CT(\s|$)", "$1")
    
    ; Restore CTA
    PriorDescript := StrReplace(PriorDescript, "CTAPLACEHOLDER", "CTA")
    
    PriorDescript := StrReplace(PriorDescript, " SIGNXED", "")
    
    ; Expand abbreviations
    PriorDescript := StrReplace(PriorDescript, " + ", " and ")
    PriorDescript := StrReplace(PriorDescript, "+", " and ")
    PriorDescript := StrReplace(PriorDescript, " imags", "")
    PriorDescript := StrReplace(PriorDescript, "Head Or Brain", "brain")
    PriorDescript := StrReplace(PriorDescript, " W/CONTRST INCL W/O", " with and without contrast")
    PriorDescript := StrReplace(PriorDescript, " W/O", " without")
    PriorDescript := StrReplace(PriorDescript, " W/ ", " with ")
    PriorDescript := StrReplace(PriorDescript, " W/", " with ")
    ; Handle " W " carefully - must be surrounded by spaces to avoid matching words containing W
    PriorDescript := RegExReplace(PriorDescript, "i)\s+W\s+", " with ")
    PriorDescript := StrReplace(PriorDescript, " W WO", " with and without")
    PriorDescript := StrReplace(PriorDescript, " WO", " without")
    PriorDescript := StrReplace(PriorDescript, " IV ", " ")
    
    ; Body part abbreviations
    PriorDescript := StrReplace(PriorDescript, "ab pe", "abdomen and pelvis")
    PriorDescript := StrReplace(PriorDescript, "abd & pelvis", "abdomen and pelvis")
    PriorDescript := StrReplace(PriorDescript, "abd/pelvis", "abdomen and pelvis")
    PriorDescript := StrReplace(PriorDescript, " abd pel ", " abdomen and pelvis ")
    PriorDescript := StrReplace(PriorDescript, "abdomen/pelvis", "abdomen and pelvis")
    PriorDescript := StrReplace(PriorDescript, "chest/abdomen/pelvis", "chest, abdomen, and pelvis")
    PriorDescript := StrReplace(PriorDescript, "Thorax", "chest")
    PriorDescript := StrReplace(PriorDescript, "thorax", "chest")
    
    ; Clean up protocol notation
    PriorDescript := StrReplace(PriorDescript, "P.E", "PE")
    PriorDescript := StrReplace(PriorDescript, "p.e", "PE")
    PriorDescript := RegExReplace(PriorDescript, "i)\s+protocol\s*$", "")  ; Remove trailing "protocol"
    
    PriorDescript := Trim(PriorDescript)
    StringLower, PriorDescript1, PriorDescript
    
    ; Reorder laterality before processing CT/CTA modifiers
    Gosub, ReorderLaterality
    
    ; Reorder: Position CT/CTA correctly
    ModifierFound := false
    HasCTA := false
    CTAMovedFromStart := false
    
    ; Check if CTA is at the start - move it after the body part
    if RegExMatch(PriorDescript1, "i)^cta\s+")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)^cta\s+", "")
        HasCTA := true
        CTAMovedFromStart := true
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " cta")
    {
        ; CTA is already in correct position
        HasCTA := true
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " angiography")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(angiography)", " CT $1")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " with and without")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(with and without)", " CT $1")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " without")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(without)", " CT $1")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " with")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s+(with)", " CT $1")
        ModifierFound := true
    }
    
    ; If CTA was at the start, insert it after the body part
    ; If laterality is present, body part is the second word; otherwise it's the first
    if (CTAMovedFromStart)
    {
        ; Check if first word is laterality (right/left/bilateral)
        if RegExMatch(PriorDescript1, "i)^(right|left|bilateral)\s+")
        {
            ; Laterality is first, so body part is second - insert CTA after second word
            PriorDescript1 := RegExReplace(PriorDescript1, "i)^((right|left|bilateral)\s+\w+)\s+", "$1 CTA ")
        }
        else
        {
            ; No laterality, body part is first word
            PriorDescript1 := RegExReplace(PriorDescript1, "i)^(\w+)\s+", "$1 CTA ")
        }
    }
    
    ; If no modifier found and no CTA, add CT at the end
    if (!ModifierFound && !HasCTA)
        PriorDescript1 := PriorDescript1 . " CT"
    
    Goto, ComparisonFill
}

; =============================================================================
; FINAL OUTPUT - ComparisonFill
; =============================================================================

ComparisonFill:
; Uppercase modality indicators when they appear as whole words
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bcta\b", "CTA")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bct\b", "CT")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bmra\b", "MRA")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bmrv\b", "MRV")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bmri\b", "MRI")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bmr\b", "MR")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bpa\b", "PA")
PriorDescript1 := RegExReplace(PriorDescript1, "i)\bpe\b", "PE")

; Check if prior study was within the last 2 days - if so, include time
IncludeTime := false
if (PriorDate != "" and PriorTimeFormatted != "")
{
    ; Parse the prior date (format: M/D/YYYY)
    RegExMatch(PriorDate, "i)(\d{1,2})/(\d{1,2})/(\d{4})", DateParts)
    PriorMonth := DateParts1
    PriorDay := DateParts2
    PriorYear := DateParts3
    
    if (PriorMonth and PriorDay and PriorYear)
    {
        PriorMonth += 0
        PriorDay += 0
        
        ; Build YYYYMMDD with proper padding
        PriorDateStamp := PriorYear
        PriorDateStamp .= SubStr("0" . PriorMonth, -1)
        PriorDateStamp .= SubStr("0" . PriorDay, -1)
        
        FormatTime, CurrentDateStamp, , yyyyMMdd
        
        DaysDiff := CurrentDateStamp
        EnvSub, DaysDiff, %PriorDateStamp%, Days
        
        if (DaysDiff >= 0 and DaysDiff <= 2)
            IncludeTime := true
    }
}

; Filter out "No Prior Report" phrase if it appears in the description
PriorDescript1 := RegExReplace(PriorDescript1, "i)\s*no prior report\.?\s*", " ", , 1)

; Build the final comparison text
if (IncludeTime)
    FinalText := " COMPARISON: " . PriorDate . " " . PriorTimeFormatted . " " . PriorDescript1 . ". " . PriorReport . PriorImages
else
    FinalText := " COMPARISON: " . PriorDate . " " . PriorDescript1 . ". " . PriorReport . PriorImages

; Activate Mosaic Info Hub window and paste
SetTitleMatchMode, 2
WinActivate, Mosaic Info Hub
WinWaitActive, Mosaic Info Hub, , 1

mosaicwinID := WinExist("Mosaic Info Hub")
if (mosaicwinID)
{
    WinActivate, ahk_id %mosaicwinID%
    WinWaitActive, ahk_id %mosaicwinID%, , 1
    
    Clipboard := FinalText
    Send ^v
    Sleep, 100
}
else
{
    MsgBox, Mosaic Info Hub window not found!
}

; Restore original clipboard
Clipboard := ClipboardBackup
Return

; =============================================================================
; SUBROUTINES
; =============================================================================

; --- Reorder Laterality (right/left/bilateral) to come before body part ---
ReorderLaterality:
    ; Check for laterality terms and move them to the beginning
    ; Pattern: "shoulder right" -> "right shoulder"
    ; Pattern: "knee left" -> "left knee"
    ; Pattern: "hand bilateral" -> "bilateral hand"
    
    ; Check if laterality is already at the start (optimization)
    if RegExMatch(PriorDescript1, "i)^(right|left|bilateral)\s+")
        Return  ; Already in correct position
    
    ; Use word boundaries to match whole words only
    if RegExMatch(PriorDescript1, "i)\b(right|left|bilateral)\b", LateralityMatch)
    {
        LateralityTerm := LateralityMatch1
        ; Remove the laterality term from its current position
        PriorDescript1 := RegExReplace(PriorDescript1, "i)\s*\b" . LateralityTerm . "\b\s*", " ", , 1)
        PriorDescript1 := Trim(PriorDescript1)
        ; Insert laterality at the beginning
        PriorDescript1 := LateralityTerm . " " . PriorDescript1
    }
Return

; --- Process Radiograph Descriptions ---
ProcessRadiograph:
    ; Expand abbreviations
    PriorDescript := StrReplace(PriorDescript, " vw", " view(s)")
    PriorDescript := StrReplace(PriorDescript, " 2v", " PA and lateral")
    PriorDescript := StrReplace(PriorDescript, " pa lat", " PA and lateral")
    PriorDescript := StrReplace(PriorDescript, " (kub)", "")
    
    PriorDescript := Trim(PriorDescript)
    StringLower, PriorDescript1, PriorDescript
    
    ; Reorder laterality before processing view modifiers
    Gosub, ReorderLaterality
    
    ; Insert "radiograph" before view modifiers
    ModifierFound := false
    
    ; Handle "3 or more radiograph views" -> "radiograph 3 or more views"
    if RegExMatch(PriorDescript1, "i)\d+\s+or\s+more\s+radiograph\s+views?")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "i)(\d+\s+or\s+more)\s+radiograph\s+(views?)", "radiograph $1 $2")
        ModifierFound := true
    }
    ; Handle numeric view patterns "1 view", "2 view", etc.
    else if RegExMatch(PriorDescript1, "\s\d+\s*view")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "(\s)(\d+\s*view)", "$1radiograph $2")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " pa and lateral")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "(\s)(pa and lateral)", "$1radiograph $2")
        ModifierFound := true
    }
    else if InStr(PriorDescript1, " view")
    {
        PriorDescript1 := RegExReplace(PriorDescript1, "(\s)(view)", "$1radiograph $2")
        ModifierFound := true
    }
    
    if (!ModifierFound)
        PriorDescript1 := PriorDescript1 . " radiograph"
Return

; --- Parse Date and Time ---
DateFill:
    ; Extract year from PriorDate
    RegExMatch(PriorDate, "i)(19[0-9][0-9]|20[0-9][0-9])", YearMatch)
    ExtractedYear := YearMatch1
    
    ; Extract day (1 or 2 digits followed by space or time)
    RegExMatch(PriorDate, "i)([0-9]{1,2})\s+[0-9:]", DayMatch)
    ExtractedDay := DayMatch1 + 0  ; Convert to number to strip leading zero
    
    ; Convert month abbreviations to numbers
    MonthNum := ""
    MonthNames := "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec"
    Loop, Parse, MonthNames, `,
    {
        if InStr(PriorDate, A_LoopField)
        {
            MonthNum := A_Index
            break
        }
    }
    
    ; Build formatted date as M/D/YYYY
    if (MonthNum != "" and ExtractedDay != "" and ExtractedYear != "")
        PriorDate := MonthNum . "/" . ExtractedDay . "/" . ExtractedYear
    
    ; Format time (remove seconds, keep timezone)
    if (PriorTime != "")
    {
        PriorTimeFormatted := RegExReplace(PriorTime, "i)(\d{1,2}:\d{2}):\d{2}", "$1")
    }
    else
    {
        PriorTimeFormatted := ""
    }
Return
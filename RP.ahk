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

; --- Insert Prior Function ---
#IfWinActive ahk_exe InteleViewer.exe
!+F3::
    Send, ^+r
Return
#if
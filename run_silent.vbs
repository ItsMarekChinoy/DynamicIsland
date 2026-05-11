' run_silent.vbs - Launches run.bat with no visible cmd window.
' To quit the app: right-click the island (or system tray icon) and choose Quit.

Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = folder
' 0 = hidden window, False = don't wait for completion
sh.Run """" & folder & "\run.bat""", 0, False

Set sh = Nothing
Set fso = Nothing

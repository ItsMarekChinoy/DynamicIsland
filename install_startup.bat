@echo off
setlocal
cd /d "%~dp0"
title Install Dynamic Island Startup

echo.
echo ================================================
echo    Add Dynamic Island to Windows Startup
echo    (silent - no cmd window)
echo ================================================
echo.

set TARGET=%~dp0run_silent.vbs
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT=%STARTUP%\Dynamic Island.lnk

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%TARGET%'; $sc.WorkingDirectory = '%~dp0'; $sc.WindowStyle = 7; $sc.Description = 'Dynamic Island for laptop (silent)'; $sc.Save()"

if exist "%SHORTCUT%" (
    echo  Done. Dynamic Island will start silently when you log in.
    echo  No cmd window will appear.
    echo.
    echo  To QUIT the app: right-click the island (or system tray icon) and pick Quit.
    echo  To remove from startup: run uninstall_startup.bat
) else (
    echo  ERROR: Could not create the shortcut.
    echo  Try running this file as administrator.
)

echo.
pause

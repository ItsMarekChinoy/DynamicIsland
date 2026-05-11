@echo off
setlocal
title Remove Dynamic Island Startup

set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Dynamic Island.lnk

if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo Removed. Dynamic Island will no longer start with Windows.
) else (
    echo Dynamic Island isn't set to auto-start. Nothing to do.
)

echo.
pause

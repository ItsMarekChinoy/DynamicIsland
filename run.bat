@echo off
setlocal
cd /d "%~dp0"
title Dynamic Island

echo.
echo ================================================
echo    DYNAMIC ISLAND
echo ================================================
echo.

REM ── Find best Python: prefer 3.12 (winsdk has wheels), then 3.13/3.11/3.10, then anything ──
set PYEXE=
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 --version >nul 2>nul
    if not errorlevel 1 set PYEXE=py -3.12
)
if "%PYEXE%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.13 --version >nul 2>nul
        if not errorlevel 1 set PYEXE=py -3.13
    )
)
if "%PYEXE%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.11 --version >nul 2>nul
        if not errorlevel 1 set PYEXE=py -3.11
    )
)
if "%PYEXE%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.10 --version >nul 2>nul
        if not errorlevel 1 set PYEXE=py -3.10
    )
)
if "%PYEXE%"=="" (
    where python >nul 2>nul
    if not errorlevel 1 set PYEXE=python
)
if "%PYEXE%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 set PYEXE=py
)
if "%PYEXE%"=="" goto no_python

echo Using:
%PYEXE% --version
echo.

echo Installing dependencies (first run only)...
echo.

%PYEXE% -m pip install --quiet --disable-pip-version-check --upgrade pip >nul 2>nul

echo  - PyQt6
%PYEXE% -m pip install --quiet --disable-pip-version-check PyQt6
if errorlevel 1 goto pyqt_failed

echo  - psutil
%PYEXE% -m pip install --quiet --disable-pip-version-check psutil

echo  - winsdk (for Spotify/media display)
%PYEXE% -m pip install --quiet --disable-pip-version-check --only-binary=:all: winsdk
if errorlevel 1 echo    [info] winsdk skipped - install Python 3.12 if you want Spotify integration

echo  - keyboard (optional global hotkey)
%PYEXE% -m pip install --quiet --disable-pip-version-check keyboard
if errorlevel 1 echo    [info] keyboard skipped - hover still works

echo.
echo ================================================
echo    Starting Dynamic Island
echo    Hover the TOP-MIDDLE EDGE of your screen
echo    Minimise this window. Closing it quits the app.
echo ================================================
echo.

%PYEXE% dynamic_island.py
echo.
echo Dynamic Island exited.
pause
exit /b 0


:no_python
echo  ERROR: Python is not installed or not on PATH.
echo.
echo  Install Python 3.12 (recommended for full Spotify support):
echo    1. Go to: https://www.python.org/downloads/release/python-3120/
echo    2. Scroll down, get "Windows installer (64-bit)"
echo    3. During install, tick "Add Python to PATH" on the first screen.
echo    4. Re-run this file.
echo.
pause
exit /b 1


:pyqt_failed
echo.
echo  ERROR: Could not install PyQt6.
echo.
echo  Most likely fixes:
echo    - Internet connection
echo    - Norton/Defender exclusions for this folder
echo    - Right-click run.bat, Run as administrator
echo    - Install Python 3.12 from python.org/downloads/release/python-3120/
echo.
pause
exit /b 1

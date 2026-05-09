#!/usr/bin/env bash
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PYEXE=python3
elif command -v python >/dev/null 2>&1; then
    PYEXE=python
else
    echo "Python 3 not found. Install it from https://www.python.org/downloads/"
    exit 1
fi

echo "Using $($PYEXE --version)"
echo "Installing dependencies (first run only)..."
$PYEXE -m pip install --quiet --disable-pip-version-check PyQt6 psutil keyboard

echo "Starting Dynamic Island. Hover the top of your screen."
$PYEXE dynamic_island.py

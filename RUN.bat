@echo off
title PS5 FFPFSC PRO
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First start: creating a private Python environment ^(one time only^)...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Python 3.11 or newer is required: https://www.python.org/downloads/
    pause
    exit /b 1
  )
)
echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo Installing dependencies failed. See the messages above.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" run.py %*

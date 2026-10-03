@echo off
title Build PS5 FFPFSC PRO
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (py -3 -m venv .venv 2>nul || python -m venv .venv)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
".venv\Scripts\python.exe" -m PyInstaller --noconfirm ps5ffpfsc.spec
if errorlevel 1 goto :fail
echo.
echo Done: dist\PS5-FFPFSC-PRO.exe
pause
exit /b 0
:fail
echo Build failed - see the messages above.
pause
exit /b 1

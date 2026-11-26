@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Bitte zuerst die lokale Einrichtung aus README.md ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\start-local.py"
pause

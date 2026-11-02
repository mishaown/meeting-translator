@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First-time setup is required. Follow the Quick start in README.md.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0local_app.py" --open
if errorlevel 1 pause

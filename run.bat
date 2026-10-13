@echo off
REM Windows: double-click to install (first time) and start the bot.
cd /d "%~dp0"
python -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is needed. Install it from python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)
REM (Re)create the environment if it is missing or broken.
.venv\Scripts\python -m pip --version >nul 2>&1
if errorlevel 1 (
  if exist .venv rmdir /s /q .venv
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create a Python environment. Install Python 3.11+ from python.org and tick "Add Python to PATH".
    if exist .venv rmdir /s /q .venv
    pause
    exit /b 1
  )
)
REM Fast when everything is installed; repairs a half-finished first install.
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Installing packages failed. Check your internet connection and run this again.
  pause
  exit /b 1
)
if not exist .env copy .env.example .env
.venv\Scripts\python -m tracker run
pause

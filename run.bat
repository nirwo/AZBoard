@echo off
echo Setting up Azure Instance Dashboard...

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed! Please install Python 3.8 or later.
    pause
    exit /b 1
)

REM Check if pip is installed
pip --version >nul 2>&1
if errorlevel 1 (
    echo pip is not installed! Please install pip.
    pause
    exit /b 1
)

REM Check if virtual environment exists, create if it doesn't
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install requirements
echo Installing requirements...
pip install -r requirements.txt

REM Create necessary directories
if not exist "logs" mkdir logs
if not exist "cache" mkdir cache

REM Run the application
echo Starting Azure Instance Dashboard...
python app.py

REM Keep the window open if there's an error
pause

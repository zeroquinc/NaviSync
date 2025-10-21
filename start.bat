@echo off
setlocal

REM Check if virtual environment exists
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
)

REM Activate the virtual environment
echo [INFO] Activating virtual environment...
call .venv\Scripts\activate

REM Install only missing dependencies
echo [INFO] Checking dependencies...
pip install --no-color --quiet --requirement requirements.txt --no-input

REM Run your script
echo [INFO] Running script...
python main.py

endlocal

pause
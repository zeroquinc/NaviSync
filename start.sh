#!/usr/bin/env bash

# NaviSync launcher script for Linux/Unix systems

PYTHON=""
PIP=""

# Check for needed tools
if which python3 > /dev/null; then
    PYTHON="python3"
elif which python > /dev/null; then
    PYTHON="python"
else
    echo "[ERROR] python3/python not found in PATH"
    exit 1
fi
if which pip > /dev/null; then
    PIP="pip"
else
    echo "[ERROR] pip not found in PATH"
    exit 1
fi

echo "[INFO] NaviSync Launcher"
echo "========================"

# Check if virtual environment exists
if [ ! -f ".venv/bin/python" ]; then
    echo "[INFO] Creating virtual environment..."
    "${PYTHON}" -m venv .venv
fi

# Activate the virtual environment  
echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

# Install only missing dependencies
echo "[INFO] Checking dependencies..."
"${PIP}" install --quiet --requirement requirements.txt

# Run the diagnostic check first
echo "[INFO] Running diagnostic check..."
"${PYTHON}" check_setup.py

echo ""
read -p "Press Enter to continue with NaviSync, or Ctrl+C to exit..."

# Run the main script
echo "[INFO] Running NaviSync..."
"${PYTHON}" main.py

echo ""
echo "[INFO] Done! Press Enter to exit..."
read

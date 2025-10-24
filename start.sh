#!/bin/bash

# NaviSync launcher script for Linux/Unix systems

echo "[INFO] NaviSync Launcher"
echo "======================"

# Check if virtual environment exists
if [ ! -f ".venv/bin/python" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate the virtual environment  
echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

# Install only missing dependencies
echo "[INFO] Checking dependencies..."
pip install --quiet --requirement requirements.txt

# Run the diagnostic check first
echo "[INFO] Running diagnostic check..."
python check_setup.py

echo ""
read -p "Press Enter to continue with NaviSync, or Ctrl+C to exit..."

# Run the main script
echo "[INFO] Running NaviSync..."
python main.py

echo ""
echo "[INFO] Done! Press Enter to exit..."
read
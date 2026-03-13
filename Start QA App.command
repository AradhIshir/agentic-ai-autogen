#!/bin/bash
# Double-click this file to start the QA app. No coding needed.

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Setup not found. Please run the one-time setup first (see HOW_TO_START.txt)."
    read -p "Press Enter to close..."
    exit 1
fi

echo "Starting QA app..."
echo "A browser window will open. Do not close this Terminal window while using the app."
echo ""

source .venv/bin/activate
streamlit run ui/app.py

echo ""
read -p "Press Enter to close this window..."

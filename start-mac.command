#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Bitte Python 3 installieren: https://www.python.org/downloads/"
  read -p "Enter zum Beenden..."
  exit 1
fi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 14943

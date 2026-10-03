#!/usr/bin/env sh
# Start PS5 FFPFSC PRO from source (Linux / macOS). Needs Python 3.11+.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First start: creating a private Python environment (one time only)..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
exec .venv/bin/python run.py "$@"

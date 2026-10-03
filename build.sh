#!/usr/bin/env sh
# Build a standalone binary (Linux) or .app bundle (macOS) into dist/.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
rm -rf build dist
.venv/bin/python -m PyInstaller --noconfirm ps5ffpfsc.spec
echo "Done: $(ls -d dist/*)"

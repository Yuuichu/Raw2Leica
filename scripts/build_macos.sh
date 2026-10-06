#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN=${PYTHON_BIN:-.venv/bin/python}
test "$(uname -s)" = Darwin
if [ ! -f .tools/exiftool/bin/exiftool ]; then
    "$PYTHON_BIN" scripts/install_exiftool.py
fi
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean --windowed \
    --name RAW2LEICA --osx-bundle-identifier com.raw2leica.desktop \
    --paths . --specpath build \
    --collect-data raw2leica --collect-all rawpy \
    --recursive-copy-metadata raw2leica \
    --add-data "$(pwd)/.tools/exiftool:.tools/exiftool" \
    scripts/app_entry.py
"$PYTHON_BIN" - <<'PY'
import plistlib
from pathlib import Path
path = Path('dist/RAW2LEICA.app/Contents/Info.plist')
info = plistlib.loads(path.read_bytes())
info.update(CFBundleShortVersionString='0.3.0', CFBundleVersion='0.3.0',
            LSMinimumSystemVersion='26.0')
path.write_bytes(plistlib.dumps(info))
PY
codesign --force --deep --sign - dist/RAW2LEICA.app
"$PYTHON_BIN" -m build
ditto -c -k --sequesterRsrc --keepParent dist/RAW2LEICA.app \
    "dist/RAW2LEICA-0.3.0-macos-$(uname -m).zip"

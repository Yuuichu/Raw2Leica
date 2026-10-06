#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN=${PYTHON_BIN:-python3}
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
if ! command -v exiftool >/dev/null 2>&1; then
  printf '\n需要 ExifTool：macOS 运行 brew install exiftool，或设置 EXIFTOOL_PATH。\n'
fi

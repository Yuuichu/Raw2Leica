#!/bin/zsh
set -eu
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  print '请先运行 scripts/setup.sh 安装运行环境。'
  read '?按回车退出…'
  exit 1
fi
exec .venv/bin/python -m raw2leica "$@"

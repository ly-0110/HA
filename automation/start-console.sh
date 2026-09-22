#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' '[ERROR] uv is not installed or not on PATH.'
  exit 1
fi
if [ ! -f web/dist/index.html ]; then
  printf '%s\n' '[ERROR] Web interface is not built. Run npm install and npm run web:build once.'
  exit 1
fi
exec uv run iot-exp-gui

#!/usr/bin/env sh
set -eu
resource_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
exec "$resource_dir/runtime/linux-x64/python/bin/python3" -I -B -X utf8 -m iot_exp.cli --resources "$resource_dir" "$@"

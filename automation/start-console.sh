#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
tools_dir="$script_dir/.tools"
export PYTHONUTF8=1
export UV_CACHE_DIR="$script_dir/.uv-cache"

for python_executable in "${IOT_EXP_PYTHON:-}" "$script_dir/.venv/bin/python" \
  "$tools_dir/python/bin/python3" python3.13 python3.12 python3.11 python3.10 python3 python; do
  [ -n "$python_executable" ] || continue
  if "$python_executable" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)' >/dev/null 2>&1; then
    exec "$python_executable" "$script_dir/console_bootstrap.py" "$@"
  fi
done

for uv_executable in "${IOT_EXP_UV:-}" "$tools_dir/uv/uv" "$tools_dir/uv" \
  "${HOME:-}/.local/bin/uv" "${HOME:-}/.cargo/bin/uv" uv; do
  [ -n "$uv_executable" ] || continue
  if command -v "$uv_executable" >/dev/null 2>&1; then
    exec "$uv_executable" run --no-project --python '>=3.10,<3.14' \
      python "$script_dir/console_bootstrap.py" "$@"
  fi
done

printf '%s\n' '未找到 Python 3.10–3.13。请安装 Python 后重新启动，项目依赖会自动安装。' \
  'Ubuntu 可运行：sudo apt install python3 python3-venv' \
  '已有 Python 时，可设置 IOT_EXP_PYTHON 为解释器的完整路径。'
exit 1

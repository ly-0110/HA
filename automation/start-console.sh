#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tools_dir="$script_dir/.tools"
cd "$script_dir"

if [ -x "$tools_dir/uv/uv" ]; then
  JAVA_HOME="$tools_dir/jre21"
  ANDROID_SDK_ROOT="$tools_dir/android-sdk"
  ANDROID_HOME="$ANDROID_SDK_ROOT"
  UV_CACHE_DIR="$script_dir/.uv-cache"
  PATH="$tools_dir/node22/bin:$JAVA_HOME/bin:$ANDROID_SDK_ROOT/platform-tools:$tools_dir/uv:$PATH"
  export JAVA_HOME ANDROID_SDK_ROOT ANDROID_HOME UV_CACHE_DIR PATH
  uv_executable="$tools_dir/uv/uv"
elif command -v uv >/dev/null 2>&1; then
  uv_executable=$(command -v uv)
else
  printf '%s\n' '[ERROR] Neither project-local .tools/uv/uv nor a global uv command was found.'
  exit 1
fi

if [ ! -f web/dist/index.html ]; then
  printf '%s\n' '[ERROR] Web interface is not built. Run npm install and npm run web:build once.'
  exit 1
fi
exec "$uv_executable" run --project "$script_dir" iot-exp-gui "$@"

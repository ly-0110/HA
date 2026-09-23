#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tools_dir="$script_dir/.tools"

export JAVA_HOME="$tools_dir/jre21"
export ANDROID_SDK_ROOT="$tools_dir/android-sdk"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export UV_CACHE_DIR="$script_dir/.uv-cache"
export PATH="$tools_dir/node22/bin:$JAVA_HOME/bin:$ANDROID_SDK_ROOT/platform-tools:$tools_dir/uv:$PATH"

exec "$tools_dir/uv/uv" run --project "$script_dir" iot-exp \
  --runtime "$script_dir/runtime/ubuntu-dev.yaml" "$@"

#!/usr/bin/env bash
set -euo pipefail
mobile_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$mobile_root/android"
if [[ ! -f app/src/main/assets/card_pose_yolov8n.onnx ]]; then
  echo 'Phone detector missing. Run scripts/prepare_models.py --android first; see mobile/README.md.' >&2
  exit 1
fi
if (( $# == 0 )); then set -- testDebugUnitTest assembleDebug; fi
if [[ -n "${TCG_WINDOWS_JAVA_HOME:-}" ]]; then
  # WSL with a Windows SDK/JDK. Pass only Gradle task names/options to cmd.exe.
  for task in "$@"; do
    [[ "$task" =~ ^[a-zA-Z0-9_:=.-]+$ ]] || { echo "Invalid Gradle argument: $task" >&2; exit 1; }
  done
  [[ "$TCG_WINDOWS_JAVA_HOME" != *'"'* && "$TCG_WINDOWS_JAVA_HOME" != *'%'* && "$TCG_WINDOWS_JAVA_HOME" != *$'\n'* ]] || exit 1
  exec cmd.exe /c "set \"JAVA_HOME=$TCG_WINDOWS_JAVA_HOME\" && gradlew.bat $*"
fi
exec bash ./gradlew "$@"

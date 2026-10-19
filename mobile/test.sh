#!/usr/bin/env bash
# Runs only the requested test group; server-writing tests require explicit selection.
set -euo pipefail
mobile_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if (( $# < 2 )); then
  echo 'Usage: bash mobile/test.sh <device-serial> ui|detector|collections [private-ip:port]' >&2
  exit 2
fi
scanner_adb="${ADB:-adb}"
serial="$1"
extra=()
case "$2" in
  ui) classes=dev.tcg.scanner.ComposeWorkflowTest,dev.tcg.scanner.LibraryTest ;;
  detector) classes=dev.tcg.scanner.CardDetectorTest ;;
  collections)
    classes=dev.tcg.scanner.CollectionsWorkflowTest
    extra=(-e collections true)
    if [[ -n "${3:-}" ]]; then extra+=(-e server "$3"); fi ;;
  *) echo 'Choose ui, detector or collections.' >&2; exit 2 ;;
esac
for apk in debug/app-debug.apk androidTest/debug/app-debug-androidTest.apk; do
  path="$mobile_root/android/app/build/outputs/apk/$apk"
  if [[ "$scanner_adb" == *.exe ]]; then path="$(wslpath -w "$path")"; fi
  "$scanner_adb" -s "$serial" install -r "$path"
done
mkdir -p "$mobile_root/android/build/reports/device"
report="$mobile_root/android/build/reports/device/$2.txt"
"$scanner_adb" -s "$serial" shell am instrument -w "${extra[@]}" -e class "$classes" \
  dev.tcg.scanner.test/androidx.test.runner.AndroidJUnitRunner | tee "$report"
if ! grep -Eq 'OK \([0-9]+ tests?\)' "$report"; then exit 1; fi

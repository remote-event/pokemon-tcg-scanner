#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
scanner_python="${TCG_PYTHON:-$project_root/.venv/bin/python}"
if [[ ! -x "$scanner_python" ]]; then
  echo 'Server environment missing. Run bash setup.sh --device cuda (or cpu).' >&2
  exit 1
fi
cd -- "$project_root"
export PYTHONPATH="$project_root/desktop${PYTHONPATH:+:$PYTHONPATH}"
exec "$scanner_python" -B -m tcg_scanner.server "$@"

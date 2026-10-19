#!/usr/bin/env bash
# Source-checkout installer. Does not change drivers, firewall rules or existing configuration.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$project_root/scripts/setup.py" "$@"

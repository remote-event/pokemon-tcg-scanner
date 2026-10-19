#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
training_python="${TCG_TRAINING_PYTHON:-$project_root/.venvs/training/bin/python}"
if [[ -z "${TCG_TRAINING_PYTHON:-}" && ! -x "$training_python" && -x "$HOME/.cache/tcg-scanner/rfdetr-venv/bin/python" ]]; then
  training_python="$HOME/.cache/tcg-scanner/rfdetr-venv/bin/python"
fi
run_dir="${TCG_TRAINING_RUN:-$project_root/artifacts/collector-number-training/run-001}"
dataset_dir="${TCG_TRAINING_DATASET:-$project_root/artifacts/collector-number-training/dataset}"
if [[ ! -x "$training_python" ]]; then
  echo 'Training environment is missing. See README.md#training-your-own-detector.' >&2
  exit 1
fi
if [[ -f "$run_dir/completed.json" ]]; then
  echo "This run has finished. Results: $run_dir"
  exit 0
fi
command_args=("$training_python" -u "$project_root/training/collector_numbers/train.py"
  --dataset "$dataset_dir" --output "$run_dir")
if [[ -f "$run_dir/last.ckpt" ]]; then
  command_args+=(--resume "$run_dir/last.ckpt")
  echo 'Resuming from the latest saved epoch.'
fi
command_args+=("$@")
mkdir -p "$run_dir/terminal-logs"
printf -v training_command '%q ' "${command_args[@]}"
echo "Saved plots: $run_dir/graphs"
# A pseudo-terminal keeps the progress bars interactive while saving the transcript.
exec script --quiet --flush --return --command "$training_command" \
  "$run_dir/terminal-logs/$(date +%Y%m%d-%H%M%S)-$$.log"

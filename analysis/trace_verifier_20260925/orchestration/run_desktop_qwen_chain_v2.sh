#!/usr/bin/env bash
set -u

workspace=/home/myl/app/guiexp/osworld
state="$workspace/experimental-results/trace_verifier_20260925/orchestration/desktop-qwen-chain-v2"
bundle="$workspace/analysis/trace_verifier_20260925/orchestration/run-code/desktop/v3"
calc="$workspace/experimental-results/trace_verifier_20260925/desktop/qwen_qwen3.8-flash/CalcTableSave/attempts/serial-rate-retry1/result.json"
mkdir -p "$state"
cp "$calc" "${calc%.json}.pre-resume-429-2.json"
date -u +%Y-%m-%dT%H:%M:%SZ > "$state/DELAY_STARTED"
sleep 900

cd "$workspace" || exit 1
set -a
source ./.trace_verifier_openrouter.env
set +a
export PYTHONPATH="$bundle/code"
export DOCKER_HOST=unix:///run/guiexp-docker.sock
export OSWORLD_CONTAINER=guiexp-osworld-l6
export OSWORLD_SERVER=http://127.0.0.1:15902

.venv-osworld/bin/python "$bundle/runner/run_three_building.py" \
  --build experimental-results/guiexp_osworld/qwen_qwen3.8-flash/CalcTableSave/build.json \
  --output-root experimental-results/trace_verifier_20260925/desktop \
  --attempt-id serial-rate-retry1 --attempt-role initial --resume \
  > "$state/calc.log" 2>&1 < /dev/null
calc_rc=$?
printf '%s\n' "$calc_rc" > "$state/calc.returncode"
calc_status=$(python3 - "$calc" <<'PY'
import json, sys
d=json.load(open(sys.argv[1])); print(d.get("terminal_status") or d.get("status"))
PY
)
if [[ "$calc_status" == "insufficient_credit" ]]; then
  printf '%s\n' "$calc" > "$state/INSUFFICIENT_CREDIT"
  exit 42
fi
if [[ "$calc_status" != "complete" ]]; then
  printf '%s\n' "calc_terminal_$calc_status" > "$state/STOPPED"
  exit "$calc_rc"
fi

.venv-osworld/bin/python "$bundle/runner/run_three_building.py" \
  --build experimental-results/guiexp_osworld/qwen_qwen3.8-flash/WriterMemoSave/build.json \
  --output-root experimental-results/trace_verifier_20260925/desktop \
  --attempt-id serial2 --attempt-role initial \
  > "$state/writer.log" 2>&1 < /dev/null
writer_rc=$?
printf '%s\n' "$writer_rc" > "$state/writer.returncode"
if [[ $writer_rc -eq 0 ]]; then
  date -u +%Y-%m-%dT%H:%M:%SZ > "$state/DONE"
fi
exit "$writer_rc"

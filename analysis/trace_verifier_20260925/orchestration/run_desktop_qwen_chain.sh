#!/usr/bin/env bash
set -u

workspace=/home/myl/app/guiexp/osworld
state="$workspace/experimental-results/trace_verifier_20260925/orchestration/desktop-qwen-chain"
calc_pid_file="$workspace/experimental-results/trace_verifier_20260925/orchestration/desktop-qwen-calc-serial1/pid"
calc_result="$workspace/experimental-results/trace_verifier_20260925/desktop/qwen_qwen3.8-flash/CalcTableSave/attempts/serial-rate-retry1/result.json"
bundle="$workspace/analysis/trace_verifier_20260925/orchestration/run-code/desktop/v3"
mkdir -p "$state"

calc_pid=$(<"$calc_pid_file")
while kill -0 "$calc_pid" 2>/dev/null; do sleep 60; done

if [[ ! -f "$calc_result" ]]; then
  printf '%s\n' "calc_missing_result" > "$state/STOPPED"
  exit 3
fi

calc_status=$(python3 - "$calc_result" <<'PY'
import json, sys
d=json.load(open(sys.argv[1])); print(d.get("terminal_status") or d.get("status"))
PY
)
if [[ "$calc_status" == "insufficient_credit" ]]; then
  printf '%s\n' "$calc_result" > "$state/INSUFFICIENT_CREDIT"
  exit 42
fi

cd "$workspace" || exit 1
set -a
source ./.trace_verifier_openrouter.env
set +a
export PYTHONPATH="$bundle/code"
export DOCKER_HOST=unix:///run/guiexp-docker.sock
export OSWORLD_CONTAINER=guiexp-osworld-l6
export OSWORLD_SERVER=http://127.0.0.1:15902

.venv-osworld/bin/python "$bundle/runner/run_three_building.py" \
  --build experimental-results/guiexp_osworld/qwen_qwen3.8-flash/WriterMemoSave/build.json \
  --output-root experimental-results/trace_verifier_20260925/desktop \
  --attempt-id serial1 --attempt-role initial \
  > "$state/writer.log" 2>&1 < /dev/null
rc=$?
printf '%s\n' "$rc" > "$state/writer.returncode"
if [[ $rc -eq 0 ]]; then
  date -u +%Y-%m-%dT%H:%M:%SZ > "$state/DONE"
fi
exit "$rc"

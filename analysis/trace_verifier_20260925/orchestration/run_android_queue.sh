#!/usr/bin/env bash
set -u

cd /home/yulong/app/guiexp/android || exit 1
export PYTHONPATH=computer-use

runner=analysis/trace_verifier_20260925/runners/android/run_cell.py
result_root=experimental-results/trace_verifier_20260925/android
log_root=experimental-results/trace_verifier_20260925/orchestration/android-queue
python=../android-pair/.venv-android/bin/python
price_sheet=measurement_update_20260918-prices-v1
mkdir -p "$log_root"

run_cell() {
  local source_cell=$1 model_slug=$2 model=$3 family=$4 attempt_id=$5 attempt_role=$6
  local out="$result_root/$model_slug/$family/$attempt_id"
  local log="$log_root/${model_slug}_${family}_${attempt_id}.log"
  mkdir -p "$out"
  "$python" "$runner" \
    --source-cell "$source_cell" --model "$model" --family "$family" \
    --attempt-id "$attempt_id" --attempt-role "$attempt_role" \
    --price-sheet-id "$price_sheet" --out "$out" \
    --console-port 8620 --grpc-port 8630 --avd guiexpPair-w0 \
    >"$log" 2>&1
  local rc=$?
  "$python" - "$out/result.json" "$rc" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
return_code = int(sys.argv[2])
if not path.is_file():
    print(json.dumps({"result": str(path), "terminal_status": "missing", "return_code": return_code}))
    raise SystemExit(3)
record = json.loads(path.read_text())
status = record.get("terminal_status") or record.get("status")
print(json.dumps({"result": str(path), "terminal_status": status,
                  "admitted": record.get("admitted"), "return_code": return_code}))
raise SystemExit(42 if status == "insufficient_credit" else 0)
PY
  local inspect_rc=$?
  if [[ $inspect_rc -eq 42 ]]; then
    printf '%s\n' "$out/result.json" > "$log_root/INSUFFICIENT_CREDIT"
    exit 42
  fi
  if [[ $inspect_rc -ne 0 ]]; then
    exit "$inspect_rc"
  fi
}

models=(
  "qwen_qwen3.8-flash|qwen/qwen3.8-flash"
  "z-ai_glm-5.3-flash|z-ai/glm-5.3-flash"
  "deepseek_deepseek-v4-flash-vision-exp|deepseek/deepseek-v4-flash-vision-exp"
)
families=(ContactsAddContact MarkorDeleteNote OsmAndMarker SimpleCalendarAddOneEvent)

for pair in "${models[@]}"; do
  IFS='|' read -r model_slug model <<<"$pair"
  for family in "${families[@]}"; do
    if [[ "$model_slug/$family" == "qwen_qwen3.8-flash/ContactsAddContact" ]]; then
      continue
    fi
    source_cell="experimental-results/guiexp_android/t16_build/$model_slug/$family"
    run_cell "$source_cell" "$model_slug" "$model" "$family" main initial
  done
done

while IFS= read -r compile_path; do
  source_cell=${compile_path%/compile.json}
  attempt_id=${source_cell##*/}
  parent=${source_cell%/*}
  family=${parent##*/}
  parent=${parent%/*}
  model_slug=${parent##*/}
  case "$model_slug" in
    qwen_qwen3.8-flash) model=qwen/qwen3.8-flash ;;
    z-ai_glm-5.3-flash) model=z-ai/glm-5.3-flash ;;
    deepseek_deepseek-v4-flash-vision-exp) model=deepseek/deepseek-v4-flash-vision-exp ;;
    *) printf 'Unknown model slug: %s\n' "$model_slug" >&2; exit 4 ;;
  esac
  run_cell "$source_cell" "$model_slug" "$model" "$family" "$attempt_id" repeat
done < <(find experimental-results/guiexp_android/t19_repeated -mindepth 4 -maxdepth 4 -name compile.json | sort)

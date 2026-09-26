#!/usr/bin/env bash
set -u

if [[ $# -ne 5 ]]; then
  echo "usage: $0 MANIFEST CONSOLE_PORT GRPC_PORT AVD LANE_ID" >&2
  exit 2
fi

manifest=$1
console_port=$2
grpc_port=$3
avd=$4
lane_id=$5
workspace=${TRACE_VERIFIER_ANDROID_WORKSPACE:-/home/yulong/app/guiexp/android}
bundle=${TRACE_VERIFIER_ANDROID_BUNDLE:-$workspace/analysis/trace_verifier_20260925/orchestration/run-code/android/v9}
bundle_id=${TRACE_VERIFIER_ANDROID_BUNDLE_ID:-trace-verifier-android-v9-d6c06629e029815d}
python=${TRACE_VERIFIER_ANDROID_PYTHON:-$workspace/../android-pair/.venv-android/bin/python}
runner=${TRACE_VERIFIER_ANDROID_RUNNER:-$bundle/analysis/trace_verifier_20260925/runners/android/run_cell.py}
log_root=$workspace/experimental-results/trace_verifier_20260925/orchestration/android-$lane_id
claim_root=${TRACE_VERIFIER_ANDROID_CLAIM_ROOT:-$workspace/experimental-results/trace_verifier_20260925/orchestration/claims}
price_sheet=measurement_update_20260918-prices-v1
mkdir -p "$log_root" "$claim_root"

while IFS='|' read -r source_cell model_slug model family attempt_id role out; do
  [[ -z "$source_cell" || "$source_cell" == \#* ]] && continue
  log="$log_root/${model_slug}_${family}_${attempt_id}.log"
  mkdir -p "$out"

  "$python" - "$out/result.json" "$model" "$family" "$attempt_id" "$role" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)
record = json.loads(path.read_text())
expected = (sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
actual = (record.get("model"), record.get("family"), record.get("attempt_id"), record.get("attempt_role"))
if actual != expected:
    print(json.dumps({"error": "identity_mismatch", "path": str(path), "expected": expected, "actual": actual}))
    raise SystemExit(12)
status = record.get("terminal_status") or record.get("status")
if status not in {None, "running", "incomplete", "provisional_initial", "provisional_repair", "provisional_resume"}:
    print(json.dumps({"result": str(path), "action": "skip_terminal", "terminal_status": status}))
    raise SystemExit(10)
print(json.dumps({"error": "explicit_resume_required", "path": str(path), "terminal_status": status}))
raise SystemExit(11)
PY
  precheck_rc=$?
  if [[ $precheck_rc -eq 10 ]]; then
    continue
  fi
  if [[ $precheck_rc -ne 0 ]]; then
    exit "$precheck_rc"
  fi

  identity="$role|$model|$family|$attempt_id"
  claim_id=$(printf '%s' "$identity" | sha256sum | cut -d' ' -f1)
  claim_dir="$claim_root/$claim_id.lock"
  if ! mkdir "$claim_dir" 2>/dev/null; then
    printf '{"error":"identity_already_claimed","identity":"%s","claim":"%s"}\n' "$identity" "$claim_dir" >&2
    exit 13
  fi
  printf '%s\n' "$identity" > "$claim_dir/identity"
  printf '%s\n' "$$" > "$claim_dir/pid"
  release_claim() {
    unlink "$claim_dir/identity" 2>/dev/null || true
    unlink "$claim_dir/pid" 2>/dev/null || true
    rmdir "$claim_dir" 2>/dev/null || true
  }
  trap release_claim EXIT INT TERM

  PYTHONPATH="$bundle/code" "$python" "$runner" \
    --source-cell "$source_cell" --model "$model" --family "$family" \
    --attempt-id "$attempt_id" --attempt-role "$role" \
    --code-bundle-id "$bundle_id" \
    --private-env-file "$workspace/.trace_verifier_openrouter.env" \
    --price-sheet-id "$price_sheet" --out "$out" \
    --console-port "$console_port" --grpc-port "$grpc_port" --avd "$avd" \
    >"$log" 2>&1 < /dev/null
  rc=$?
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
if status == "insufficient_credit":
    raise SystemExit(42)
if status not in {"complete", "refused", "timeout", "operator_aborted"}:
    raise SystemExit(4)
PY
  inspect_rc=$?
  if [[ $inspect_rc -eq 42 ]]; then
    printf '%s\n' "$out/result.json" > "$log_root/INSUFFICIENT_CREDIT"
    exit 42
  fi
  if [[ $inspect_rc -ne 0 ]]; then
    exit "$inspect_rc"
  fi
  release_claim
  trap - EXIT INT TERM
done < "$manifest"

#!/usr/bin/env bash
set -u

if [[ $# -ne 5 ]]; then
  echo "usage: $0 MANIFEST CONSOLE_PORT GRPC_PORT AVD LANE_ID" >&2
  exit 2
fi

workspace=/home/yulong/app/guiexp/android
launcher="$workspace/analysis/trace_verifier_20260925/orchestration/run_android_manifest.sh"
manifest_hashes="$workspace/analysis/trace_verifier_20260925/orchestration/QUEUE_MANIFESTS.sha256"
state="$workspace/experimental-results/trace_verifier_20260925/orchestration/android-$5-supervisor"
mkdir -p "$state"

(cd "$workspace" && sha256sum -c "$manifest_hashes" >/dev/null) || {
  printf '%s\n' "manifest_hash_verification_failed" > "$state/STOPPED"
  exit 14
}

while true; do
  if ps -eo comm=,args= | awk -v port="--console-port $2" '
      $1 ~ /python/ && index($0, "run_cell.py") && index($0, port) { found=1 }
      END { exit !found }
    '; then
    printf '{"timestamp":"%s","state":"lane_busy","console_port":%d}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" >> "$state/status.jsonl"
    sleep 60
    continue
  fi
  "$launcher" "$1" "$2" "$3" "$4" "$5" >> "$state/attempts.jsonl" 2>> "$state/errors.log" < /dev/null
  rc=$?
  printf '{"timestamp":"%s","return_code":%d}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc" >> "$state/status.jsonl"
  case "$rc" in
    0) exit 0 ;;
    11|13) sleep 60 ;;
    42) printf '%s\n' "insufficient_credit" > "$state/STOPPED"; exit 42 ;;
    *) printf '%s\n' "launcher_error_$rc" > "$state/STOPPED"; exit "$rc" ;;
  esac
done

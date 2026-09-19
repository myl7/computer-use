#!/bin/bash
# Rerun queue for the cells the 2026-09-10 incidents spoiled. Run ONLY after
# run_batch.sh has exited (one emulator, one client at a time).
#
#   GLM OsmAndMarker              killed by a failed lldb attach inside verification;
#                                 exploration/translator/builder/gate_per_k are on disk,
#                                 so this one resumes. Cell cap raised to 1.00 because
#                                 the recorded stages already carry 0.416 USD.
#   GLM FilesMoveFile             crashed at 13:50 (a11y tree lost while pytest drove
#                                 the same emulator). Fresh run, old dir set aside.
#   GLM SimpleCalendarAddOneEvent crashed in translator at ~14:06 for the same reason.
#                                 Fresh run, old dir set aside.
#   DS  MarkorDeleteNote          started inside the second pytest window, two failed
#                                 exploration attempts, killed at 14:16. Fresh run.
#   DS  MarkorCreateNote          unhandled RuntimeError at 19:48: call_builder got an
#                                 empty reply from DeepSeek 3 times in a row during a
#                                 repair round inside verification. exploration/
#                                 translator/builder/gate_per_k are on disk and were
#                                 never touched by the failure, so this one resumes;
#                                 verification (and past it) reruns fresh. If it hits
#                                 the same empty-reply crash again, that is a real
#                                 DeepSeek-flakiness gap in call_builder's retry (3
#                                 attempts, no fallback to a failed-round record) that
#                                 needs a code fix, not another blind rerun.
#   DS  OsmAndMarker              hit its own $0.60 cell cap after verification (spent
#                                 $1.0786) and stopped by design (BudgetExceeded, not a
#                                 bug). exploration/translator/builder/gate_per_k/
#                                 verification are on disk, so this one resumes with the
#                                 cap raised to 1.70 to leave headroom for deploy+doc_arm.
#
# Writes rerun_progress.json next to progress.json and appends to rerun.log.

cd /Users/myl/app/computer-use/computer-use || exit 1
ENV_FILE=/Users/myl/app/.env
[ -f /Users/myl/app/computer-use/.env ] && ENV_FILE=/Users/myl/app/computer-use/.env
set -a; . "$ENV_FILE"; set +a
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY missing from $ENV_FILE}"
: "${OPENROUTER_BASE_URL:?OPENROUTER_BASE_URL missing from $ENV_FILE}"
PY=../.venv-android/bin/python
OUT_ROOT=../experimental-results/guiexp_android/t16_build
PROGRESS="$OUT_ROOT/rerun_progress.json"
STAMP=$(date '+%Y%m%d-%H%M')

if kill -0 "$(cat "$OUT_ROOT/batch.pid" 2>/dev/null)" 2>/dev/null; then
  echo "run_batch.sh is still alive; refusing to share the emulator" >&2
  exit 2
fi

cell_cost () {
  $PY - "$1/build.json" <<'EOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print(0); sys.exit()
print(d.get("total_cost_usd") or d.get("cost_usd") or 0)
EOF
}

write_progress () {
  cat > "$PROGRESS" <<EOF
{"cells_done": $1, "cells_total": ${TOTAL:-5}, "spend_usd": $2, "last_cell": "$3", "last_status": "$4", "updated": "$(date '+%Y-%m-%dT%H:%M:%S')"}
EOF
}

# family | model | mode(resume|fresh) | cell cap
# NOTE 2026-09-11: GLM OsmAndMarker already finished cleanly on the first run of this
# queue (rc=0, resumed, doc_arm reached) — dropped from the list below. The first run
# also stopped after that one cell: piping "$QUEUE" into `while read` shares the loop's
# stdin with every command inside it, including the build_protocol subprocess, so once
# that subprocess (or something in its call chain) touched stdin the next `read` hit
# EOF and the loop silently ended after one iteration. Fixed by switching to a plain
# array + for loop below, which never reads from stdin at all.
QUEUE=(
  "FilesMoveFile|z-ai/glm-5.3-flash|fresh|0.60"
  "SimpleCalendarAddOneEvent|z-ai/glm-5.3-flash|fresh|0.60"
  "MarkorDeleteNote|deepseek/deepseek-v4-flash-vision-exp|fresh|0.60"
  "MarkorCreateNote|deepseek/deepseek-v4-flash-vision-exp|resume|0.60"
  "OsmAndMarker|deepseek/deepseek-v4-flash-vision-exp|resume|1.70"
)
TOTAL=${#QUEUE[@]}

DONE=0; SPENT=0
write_progress 0 0 none starting
echo "=== rerun queue started $(date '+%Y-%m-%dT%H:%M:%S')"
for ENTRY in "${QUEUE[@]}"; do
  IFS='|' read -r FAM MODEL MODE CAP <<< "$ENTRY"
  SLUG=$(echo "$MODEL" | tr '/' '_')
  DEST="$OUT_ROOT/$SLUG/$FAM"
  EXTRA=""
  if [ "$MODE" = "resume" ]; then
    EXTRA="--resume"
  elif [ -d "$DEST" ]; then
    mv "$DEST" "$DEST.spoiled-$STAMP"
    echo "=== set aside $DEST -> $DEST.spoiled-$STAMP"
  fi
  echo ""
  echo "=== [$((DONE+1))/$TOTAL] $MODEL $FAM ($MODE, cap $CAP)  $(date '+%H:%M:%S')"
  write_progress "$DONE" "$SPENT" "$SLUG/$FAM" running
  $PY -m guiexp_android.build_protocol \
    --family "$FAM" --model "$MODEL" --seeds 1,2,3 --k 1,2,3 \
    --deploy-uses 30 --doc-seeds 4,5,6 --max-cost-usd "$CAP" \
    --out "$DEST" --keep-emulator $EXTRA < /dev/null
  RC=$?
  C=$(cell_cost "$DEST")
  SPENT=$($PY -c "print(round($SPENT + $C, 6))")
  DONE=$((DONE+1))
  STATUS="ok"; [ $RC -ne 0 ] && STATUS="failed_rc$RC"
  echo "=== cell $SLUG/$FAM finished rc=$RC, cost $C USD, queue cumulative $SPENT USD"
  write_progress "$DONE" "$SPENT" "$SLUG/$FAM" "$STATUS"
done
echo "=== rerun queue finished $(date '+%Y-%m-%dT%H:%M:%S')"

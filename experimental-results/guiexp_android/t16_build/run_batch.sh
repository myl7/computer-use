#!/bin/bash
# Step 5: the full build batch, seven families x two models, sequential.
#
# One cell = one build_protocol run. Order is build_protocol.BATCH_FAMILIES
# (cheapest step cap first) with GLM before DeepSeek, so a budget stop loses
# the least work. Per-cell cap USD 0.60; the batch stops when cumulative
# spend crosses OVERALL_CAP.
#
# Progress is rewritten to t16_build/progress.json after every cell.
set -u

cd /Users/myl/app/computer-use/code || exit 1

# API keys: the runner reads OPENROUTER_* straight out of the environment.
ENV_FILE=/Users/myl/app/.env
[ -f /Users/myl/app/computer-use/.env ] && ENV_FILE=/Users/myl/app/computer-use/.env
set -a; . "$ENV_FILE"; set +a
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY missing from $ENV_FILE}"
: "${OPENROUTER_BASE_URL:?OPENROUTER_BASE_URL missing from $ENV_FILE}"
PY=../.venv-android/bin/python
OUT_ROOT=../experimental-results/guiexp_android/t16_build
PROGRESS="$OUT_ROOT/progress.json"
OVERALL_CAP=5.00
CELL_CAP=0.60

FAMILIES="MarkorDeleteNote ContactsAddContact OsmAndFavorite MarkorCreateNote OsmAndMarker FilesMoveFile SimpleCalendarAddOneEvent"
MODELS="z-ai/glm-5.3-flash deepseek/deepseek-v4-flash-vision-exp"

TOTAL_CELLS=$(( $(echo $FAMILIES | wc -w) * $(echo $MODELS | wc -w) ))
DONE=0
SPENT=0

write_progress () {
  $PY - "$PROGRESS" "$1" "$2" "$3" "$4" "$5" "$6" <<'PYEOF'
import json, sys, time
path, done, total, spent, cell, status, started = sys.argv[1:8]
json.dump({
    "cells_done": int(done),
    "cells_total": int(total),
    "spend_usd": float(spent),
    "overall_cap_usd": 5.00,
    "last_cell": cell,
    "last_status": status,
    "batch_started": started,
    "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
}, open(path, "w"), indent=1)
PYEOF
}

cell_cost () {
  # Sum the USD this cell actually spent, out of its build.json.
  $PY - "$1" <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / "build.json"
if not p.exists():
    print("0.0"); raise SystemExit
try:
    d = json.loads(p.read_text())
except Exception:
    print("0.0"); raise SystemExit
def walk(x):
    if isinstance(x, dict):
        if "cost_usd" in x and isinstance(x["cost_usd"], (int, float)):
            yield float(x["cost_usd"])
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)
tot = d.get("total_cost_usd")
print(f"{float(tot) if isinstance(tot,(int,float)) else max(list(walk(d)) or [0.0]):.6f}")
PYEOF
}

STARTED=$(date "+%Y-%m-%dT%H:%M:%S")
write_progress 0 "$TOTAL_CELLS" 0 "none" "starting" "$STARTED"
echo "=== batch start $STARTED, $TOTAL_CELLS cells, overall cap USD $OVERALL_CAP"

for MODEL in $MODELS; do
  SLUG=$(echo "$MODEL" | tr '/' '_')
  for FAM in $FAMILIES; do
    CELL="$SLUG/$FAM"
    OVER=$($PY -c "print(1 if $SPENT >= $OVERALL_CAP else 0)")
    if [ "$OVER" = "1" ]; then
      echo "=== STOP: cumulative spend $SPENT USD reached the overall cap $OVERALL_CAP"
      write_progress "$DONE" "$TOTAL_CELLS" "$SPENT" "$CELL" "stopped_over_budget" "$STARTED"
      echo "=== batch stopped $(date '+%Y-%m-%dT%H:%M:%S')"
      exit 0
    fi

    DEST="$OUT_ROOT/$SLUG/$FAM"
    if [ -f "$DEST/build.json" ]; then
      C=$(cell_cost "$DEST")
      SPENT=$($PY -c "print(round($SPENT + $C, 6))")
      DONE=$((DONE+1))
      echo ""
      echo "=== [$DONE/$TOTAL_CELLS] skip (build.json already recorded): $CELL, cost $C USD, cumulative $SPENT USD"
      write_progress "$DONE" "$TOTAL_CELLS" "$SPENT" "$CELL" "skipped_recorded" "$STARTED"
      continue
    fi
    echo ""
    echo "=== [$((DONE+1))/$TOTAL_CELLS] $MODEL $FAM  (spent so far $SPENT USD)  $(date '+%H:%M:%S')"
    write_progress "$DONE" "$TOTAL_CELLS" "$SPENT" "$CELL" "running" "$STARTED"

    $PY -m guiexp_android.build_protocol \
      --family "$FAM" --model "$MODEL" --seeds 1,2,3 --k 1,2,3 \
      --deploy-uses 30 --doc-seeds 4,5,6 --max-cost-usd "$CELL_CAP" \
      --out "$DEST" --keep-emulator
    RC=$?

    C=$(cell_cost "$DEST")
    SPENT=$($PY -c "print(round($SPENT + $C, 6))")
    DONE=$((DONE+1))
    STATUS="ok"; [ $RC -ne 0 ] && STATUS="failed_rc$RC"
    echo "=== cell $CELL finished rc=$RC, cost $C USD, cumulative $SPENT USD"
    write_progress "$DONE" "$TOTAL_CELLS" "$SPENT" "$CELL" "$STATUS" "$STARTED"
  done
done

write_progress "$DONE" "$TOTAL_CELLS" "$SPENT" "all" "finished" "$STARTED"
echo "=== batch finished $(date '+%Y-%m-%dT%H:%M:%S'), $DONE cells, $SPENT USD"

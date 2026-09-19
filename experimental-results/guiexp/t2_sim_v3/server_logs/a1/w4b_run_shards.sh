#!/bin/bash
# W7b: E11 a1 rerun shard driver (gate_prior=add_one via constants
# constants.measured.v3.a1.json).  Same shard geometry as W4 (one grid
# point per shard), separate out_a1/logs_a1 trees; W4 outputs untouched.
# Usage: w4b_run_shards.sh <specfile> <cost_set> <c_fail_ratio> <parallel_shards> <jobs_per_shard>
set -u
SPEC=$1; CS=$2; RATIO=$3; PAR=$4; JOBS=$5
BASE=$HOME/app/guiexp/sim_w4
CODE=$BASE/app/computer-use/t2sim
OUT=$BASE/out_a1
LOGS=$BASE/logs_a1
MASTER=$LOGS/shard_master.log
export BASE CODE OUT LOGS MASTER CS RATIO JOBS

shard_one() {
  id=$1; spec=$2
  jf=$OUT/E11_env_fragility_${id}.json
  cf=$OUT/E11_env_fragility_${id}.csv
  mf=$OUT/E11_env_fragility_${id}.md
  if [ -s "$jf" ] && [ -s "$cf" ] && [ -s "$mf" ]; then
    echo "[skip] $id spec=$spec $(date '+%F %T')" >> "$MASTER"; return 0
  fi
  rm -f "$jf" "$cf" "$mf"
  echo "[start] $id spec=$spec cs=$CS ratio=$RATIO $(date '+%F %T')" >> "$MASTER"
  cd "$CODE" && python3 sweep_env_fragility.py \
    --constants constants.measured.v3.a1.json --cost-set "$CS" \
    --only "$spec" --reps 10 --seed 7 --jobs "$JOBS" --bootstrap 2000 \
    --c-fail-ratio "$RATIO" \
    --out-dir "$OUT" --tag "$id" > "$LOGS/shard_${id}.log" 2>&1
  rc=$?
  echo "[end rc=$rc] $id $(date '+%F %T')" >> "$MASTER"
}
export -f shard_one

echo "=== phase cs=$CS ratio=$RATIO par=$PAR jobs=$JOBS start $(date '+%F %T')" >> "$MASTER"
awk '{print $1, $2}' "$SPEC" | xargs -P "$PAR" -n 2 bash -c 'shard_one "$0" "$1"'
echo "=== phase cs=$CS done $(date '+%F %T')" >> "$MASTER"

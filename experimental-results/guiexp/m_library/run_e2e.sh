#!/bin/zsh
# E2E library-manifest probe: discover/wizard GLM-5.3-flash screenshot+ax,
# max 60 steps, seeds {0,1} x manifest n in {0,20,100}  (6 episodes),
# plus two one-call `floor` episodes (no manifest / n=100 manifest) to pin
# down floor(0) and the per-call manifest token delta m*n on this prompt path.
set -u
set -a
source ~/app/.env
set +a

REPO=/Users/myl/app/computer-use
GUI=$REPO/computer-use
PY=$REPO/.venv-gui/bin/python
LIB=$REPO/experimental-results/guiexp/m_library
OUT=$LIB/e2e
mkdir -p "$OUT"

cd "$GUI"

# quick floor probes first (one model call each)
$PY -m guiexp.runner --layout wizard --condition floor --seed 0 \
    --model z-ai/glm-5.3-flash --obs-mode screenshot+ax --max-steps 1 \
    --out "$OUT/floor_n0_s0" > "$OUT/floor_n0_s0.log" 2>&1
$PY -m guiexp.runner --layout wizard --condition floor --seed 0 \
    --model z-ai/glm-5.3-flash --obs-mode screenshot+ax --max-steps 1 \
    --manifest "$LIB/manifest_n100.txt" \
    --out "$OUT/floor_n100_s0" > "$OUT/floor_n100_s0.log" 2>&1

for seed in 0 1; do
  for n in 0 20 100; do
    tag="discover_n${n}_s${seed}"
    args=(--layout wizard --condition discover --seed $seed
          --model z-ai/glm-5.3-flash --obs-mode screenshot+ax --max-steps 60
          --out "$OUT/$tag")
    if [ "$n" -gt 0 ]; then
      args+=(--manifest "$LIB/manifest_n${n}.txt")
    fi
    echo "=== $tag $(date +%H:%M:%S) ===" >> "$OUT/e2e_driver.log"
    $PY -m guiexp.runner "${args[@]}" > "$OUT/$tag.log" 2>&1
    echo "=== done $tag rc=$? $(date +%H:%M:%S) ===" >> "$OUT/e2e_driver.log"
  done
done
echo "ALL DONE $(date +%H:%M:%S)" >> "$OUT/e2e_driver.log"

#!/bin/bash
# Trial: 2 discover episodes per model on CommentPost (seeds 21, 22 --
# outside every measurement namespace). Cost sanity gate before the full
# builds. Run on cs11369a under nohup.
set -u
cd ~/app/guiexp/webarena
source venv/bin/activate
set -a
source .env
set +a
export DOCKER_HOST=unix:///run/guiexp-docker.sock
export PYTHONUNBUFFERED=1

mkdir -p logs trial

run_episode() {
  model="$1"; seed="$2"; tag="$3"
  echo "=== trial $tag seed $seed $(date -Is) ==="
  python3 -m guiexp_webarena.runner \
    --family CommentPost --condition discover --seed "$seed" \
    --model "$model" --obs-mode screenshot+ax --max-steps 20 \
    --out "trial/$tag/s$seed" || echo "episode exited nonzero (recorded)"
}

run_episode z-ai/glm-5.3-flash 21 z-ai_glm-5.3-flash
run_episode z-ai/glm-5.3-flash 22 z-ai_glm-5.3-flash
run_episode deepseek/deepseek-v4-flash-vision-exp 21 deepseek_deepseek-v4-flash-vision-exp
run_episode deepseek/deepseek-v4-flash-vision-exp 22 deepseek_deepseek-v4-flash-vision-exp

echo "=== trial done $(date -Is) ==="
python3 - <<'PY'
import json
from pathlib import Path
total = 0.0
for traj in sorted(Path("trial").glob("*/*/trajectory.jsonl")):
    recs = [json.loads(l) for l in traj.read_text().splitlines() if l.strip()]
    final = [r for r in recs if r.get("record_type") == "final"][-1]
    calls = [r["usage"] for r in recs if r.get("usage")]
    cost = sum(u.get("cost_usd") or 0.0 for u in calls)
    prompt = sum(u.get("prompt_tokens") or 0 for u in calls)
    cached = sum(u.get("cached_tokens") or 0 for u in calls)
    comp = sum(u.get("completion_tokens") or 0 for u in calls)
    total += cost
    print(f"{traj.parent.parent.name}/s{final['seed']}: success={final.get('success')} "
          f"steps={final.get('steps')} calls={len(calls)} "
          f"prompt={prompt} cached={cached} completion={comp} cost=${cost:.4f}")
print(f"trial total: ${total:.4f} = {total*7.8:.2f} HKD")
PY
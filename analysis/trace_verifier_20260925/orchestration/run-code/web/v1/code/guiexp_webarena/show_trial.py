"""Print one trial trajectory's actions + per-call cache behavior."""
import json
import sys
from pathlib import Path

base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
for p in sorted(base.glob("trial/*/*/trajectory.jsonl")):
    recs = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    print(f"== {p.parent.parent.name}/s{p.parent.name[1:]} ==")
    for r in recs:
        if r.get("record_type") == "final":
            keys = {k: r[k] for k in ("success", "steps", "model_calls", "total_cost_usd")
                    if k in r}
            print("  final:", keys)
        elif r.get("action"):
            meta = r.get("obs_meta") or {}
            err = f" ERR={str(meta.get('last_action_error'))[:50]}" if meta.get("last_action_error") else ""
            print(f"  step {r['step']}: {r['action'][:90]} -> {str(meta.get('url'))[:58]}{err}")
    us = [r["usage"] for r in recs if r.get("usage")]
    print("  per-call (prompt,cached,completion,cost):",
          [(u.get("prompt_tokens"), u.get("cached_tokens"), u.get("completion_tokens"),
            round(u.get("cost_usd") or 0, 5)) for u in us])
    print()
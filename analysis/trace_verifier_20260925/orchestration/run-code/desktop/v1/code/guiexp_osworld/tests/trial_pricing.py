"""Stage 4: trial pricing -- 2 full episodes per model (< 5 HKD budget).

Per model: one CalcTableSave and one WriterMemoSave discover episode (seed 1,
step caps 22/18, the same episode shape the exploration stage will pay for).
Reports per-episode cost, wall time and the Android-arm comparison
(t12_grid GLM ContactsAddContact s1: $0.0108 / 114k tokens), and the
> 2x-alarm ruling for the log.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from guiexp_osworld import guest_env  # noqa: E402
from guiexp_osworld.explore import episode_usage, step_cap  # noqa: E402
from guiexp_osworld.run_batch import load_env_file  # noqa: E402
from guiexp_osworld.runner import run_episode  # noqa: E402

MODELS = (
    "z-ai/glm-5.3-flash",
    "deepseek/deepseek-v4-flash-vision-exp",
)
ANDROID_REF_USD = 0.0108  # t12_grid GLM ContactsAddContact s1
ALARM_MULTIPLE = 2.0


def main() -> int:
    load_env_file("/Users/myl/app/.env")
    out_root = Path("../experimental-results/guiexp_osworld/trial")
    out_root.mkdir(parents=True, exist_ok=True)
    env = guest_env.OSWorldEnv(start_if_needed=False)
    report = {"episodes": [], "android_ref_usd": ANDROID_REF_USD}
    try:
        for model in MODELS:
            for family in ("CalcTableSave", "WriterMemoSave"):
                out = out_root / model.replace("/", "_") / f"{family}__discover__s1"
                t0 = time.time()
                final = run_episode(
                    family=family, condition="discover", seed=1, model=model,
                    obs_mode="screenshot+ax", max_steps=step_cap(family),
                    out_dir=out, env=env, close_env=False,
                )
                wall = round(time.time() - t0, 1)
                usage = episode_usage(out / "trajectory.jsonl")
                episode = {
                    "model": model, "family": family, "seed": 1,
                    "success": final.get("success"), "steps": final.get("steps"),
                    "model_calls": final.get("model_calls"),
                    "total_tokens": usage["total_tokens"],
                    "prompt_tokens": usage["prompt_tokens"],
                    "cached_tokens": usage["cached_tokens"],
                    "completion_tokens": usage["completion_tokens"],
                    "cost_usd": usage["cost_usd"],
                    "wall_s": wall,
                }
                episode["vs_android"] = round(usage["cost_usd"] / ANDROID_REF_USD, 2)
                episode["alarm"] = usage["cost_usd"] > ALARM_MULTIPLE * ANDROID_REF_USD
                report["episodes"].append(episode)
                print(json.dumps(episode), flush=True)
    finally:
        env.close()
    total = sum(e["cost_usd"] for e in report["episodes"])
    report["total_cost_usd"] = round(total, 6)
    report["total_cost_hkd"] = round(total * 7.8, 2)
    report["any_alarm"] = any(e["alarm"] for e in report["episodes"])
    (out_root / "trial.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in
                      ("total_cost_usd", "total_cost_hkd", "any_alarm")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

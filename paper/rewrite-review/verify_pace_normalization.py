"""Verify PACE ratios against retained costs and report direct savings.

Run from any directory. No experiment or external API calls are made.
"""
from pathlib import Path
import hashlib
import json
import math
import random
import statistics

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "analysis/pace_edit_20260923/uncertainty"
PACE = "safe_projected_025"


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8), (a, b)


def quantile(values, p):
    values = sorted(values)
    index = p * (len(values) - 1)
    lo = int(index)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (index - lo) * (values[hi] - values[lo])


def main():
    provenance = json.loads((ANALYSIS / "provenance.json").read_text())
    online_path = ANALYSIS / "online-uncertainty.json"
    assert hashlib.sha256(online_path.read_bytes()).hexdigest() == provenance["outputs_sha256"][online_path.name]
    online = json.loads(online_path.read_text())["online"]
    checked = 0
    source_hashes = {}
    logs = []
    for group, cells in online["cells"].items():
        for cell in cells:
            rows = {}
            for source in cell["sources"]:
                content = (ROOT / source).read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                assert digest == provenance["source_sha256"][source], source
                source_hashes[source] = digest
                raw = json.loads(content)
                for policy, observations in raw["rows"].items():
                    costs = [r["cost"] for r in observations]
                    if policy in rows:
                        assert costs == rows[policy], (source, policy)
                    rows[policy] = costs
            assert all(len(v) == cell["repetitions"] for v in rows.values())
            means = {p: statistics.mean(v) for p, v in rows.items()}
            for policy, record in cell["policies"].items():
                close(means[policy], record["mean_cost"])
                close(means[policy] / means[PACE], record["ratio_pace"]["estimate"])
                if policy == PACE:
                    assert record["ratio_pace"]["ci95"] == [1.0, 1.0]
                checked += 1
            if group == "real_arrivals":
                logs.append((cell["key"], rows, means))

    # Pair numerator and denominator within each resample, with the same
    # repetition indices across all conditions as in the original analysis.
    seed = 20260922017
    rng = random.Random(seed)
    samples = [[rng.randrange(10) for _ in range(10)] for _ in range(2000)]
    savings = {}
    for policy in ("reactive", "autorpa_once", "toolpro_cost"):
        points = []
        ratios = []
        draws = [0.0] * len(samples)
        for key, rows, means in logs:
            points.append(100 * (1 - means[PACE] / means[policy]))
            ratios.append(means[policy] / means[PACE])
            for i, sample in enumerate(samples):
                pace = sum(rows[PACE][j] for j in sample)
                baseline = sum(rows[policy][j] for j in sample)
                draws[i] += 100 * (1 - pace / baseline) / len(logs)
        savings[policy] = {
            "mean_percent_saved": statistics.mean(points),
            "ci95_percent_saved": [quantile(draws, p) for p in (0.025, 0.975)],
            "mean_baseline_over_pace": statistics.mean(ratios),
            "condition_percent_saved": dict(zip((x[0] for x in logs), points)),
        }
        close(statistics.mean(ratios), online["summaries"]["real_arrivals"]["by_model"]["all_models"][policy]["mean_ratio_pace"]["estimate"])
    report = {
        "normalization": "Within each condition, divide each policy's repetition-mean cost by PACE's repetition-mean cost, then take the equally weighted mean or maximum across conditions.",
        "savings": "Equally weighted mean of 100 * (1 - mean_cost_PACE / mean_cost_baseline) across the twelve recorded-arrival conditions. This is not the inverse of the mean baseline/PACE ratio.",
        "bootstrap_seed": seed,
        "bootstrap_samples": len(samples),
        "verified_policy_condition_pairs": checked,
        "verified_sources": len(source_hashes),
        "source_sha256": source_hashes,
        "real_arrival_results": savings,
    }
    (ANALYSIS / "pace-normalization-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Verified {checked} policy-condition pairs from {len(source_hashes)} retained source files.")
    for policy, result in savings.items():
        print(f"{policy}: PACE saves {result['mean_percent_saved']:.4f}%.")


if __name__ == "__main__":
    main()

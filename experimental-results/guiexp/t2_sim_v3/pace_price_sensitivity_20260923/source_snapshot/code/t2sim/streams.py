"""Arrival-stream loaders for the T2 simulation layer.

Three kinds of stream:

  * synthetic patterns (Poisson / Zipf / bursty / regime_shift) over a set of
    family names -- gen_stream is a verbatim port of policy_sim.gen_stream so
    validate.py reproduces the old Table 2 draw-for-draw;
  * the two Wikipedia streams from datasets/wiki-stream/ ({ts, family_id}
    JSONL, one edit per line; sorted by ts, optionally windowed);
  * the old process logs (Sepsis / Helpdesk / BPI 2019) from the old
    real_streams.json format ({key: {"stream": [...], ...}}).

Loaded streams are cached in-process; E4 fires four price cells per stream
and multiprocessing workers each load a stream at most once.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_DIR = REPO_ROOT / "datasets" / "wiki-stream"
# The old harness's replay logs.  First location is the pre-2026-09-20 repo
# layout; the fallback is where the 2026-09-20 reorganization moved them
# (misc/ is the not-part-of-the-supplementary graveyard).
_OLD_REAL_STREAMS_CANDIDATES = (
    REPO_ROOT / "experimental-results" / "openapps" / "real-streams"
    / "real_streams.json",
    REPO_ROOT / "misc" / "results-dead" / "openapps" / "real-streams"
    / "real_streams.json",
)
OLD_REAL_STREAMS = next((p for p in _OLD_REAL_STREAMS_CANDIDATES
                         if p.exists()), _OLD_REAL_STREAMS_CANDIDATES[0])

_CACHE: dict = {}


# ---------------------------------------------------------------------------
# synthetic patterns (port of openapps-exp/policy_sim.py gen_stream)
# ---------------------------------------------------------------------------

def gen_stream(pattern: str, n_arrivals: int, families: list[str],
               rng) -> list[str]:
    """An arrival sequence of family names."""
    if pattern == "poisson":
        # uniform rate over families
        return [rng.choice(families) for _ in range(n_arrivals)]
    if pattern == "zipf":
        # a few hot families, many cold ones (rank 1..k, s=1.3)
        k = len(families)
        ws = [1.0 / (i + 1) ** 1.3 for i in range(k)]
        tot = sum(ws)
        return [rng.choices(families, weights=[w / tot for w in ws])[0]
                for _ in range(n_arrivals)]
    if pattern == "bursty":
        # families take turns being hot for a burst, then go cold
        out, hot, left = [], rng.choice(families), rng.randint(8, 25)
        for _ in range(n_arrivals):
            if left == 0:
                hot = rng.choice(families)
                left = rng.randint(8, 25)
            out.append(hot if rng.random() < 0.85 else rng.choice(families))
            left -= 1
        return out
    if pattern == "regime_shift":
        # one family is hot for the first half and then goes permanently
        # cold, still trickling in at 3%; another family takes over. The
        # cold family's accumulated evidence stays true of the past and
        # false of the future, which is what a half-life is for.
        half = n_arrivals // 2
        k = len(families)
        first_hot, second_hot = families[0], families[-1]

        def weights(hot: str, cold: str | None) -> list[float]:
            w = []
            for f in families:
                if f == hot:
                    w.append(0.85)
                elif f == cold:
                    w.append(0.03)
                else:
                    w.append(0.0)
            rest = 1.0 - sum(w)
            free = [i for i, f in enumerate(families)
                    if f != hot and f != cold]
            for i in free:
                w[i] = rest / max(1, len(free))
            return w

        w_a = weights(first_hot, None)
        w_b = weights(second_hot, first_hot)
        return [rng.choices(families, weights=(w_a if t < half else w_b))[0]
                for t in range(n_arrivals)]
    raise ValueError(pattern)


ALL_PATTERNS = ("poisson", "zipf", "bursty", "regime_shift")


# ---------------------------------------------------------------------------
# real streams
# ---------------------------------------------------------------------------

def _load_wiki(path: Path, window: int | None) -> list[str]:
    rows: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rows.append((obj["ts"], obj["family_id"]))
    # The frozen snapshots are already sorted by (timestamp, rcid); a stable
    # re-sort by ts is a cheap guarantee of arrival order for any re-pull.
    rows.sort(key=lambda r: r[0])
    stream = [fam for _, fam in rows]
    if window is not None:
        stream = stream[:window]
    return stream


def _load_old_real_streams() -> dict[str, list[str]]:
    data = json.loads(OLD_REAL_STREAMS.read_text())
    return {k: v["stream"] for k, v in data.items()}


def load_stream(spec: dict) -> list[str]:
    """Load a stream from a constants['streams'] entry (cached in-process)."""
    cache_key = json.dumps(spec, sort_keys=True)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    fmt = spec["format"]
    if fmt == "wiki_jsonl":
        path = Path(spec["path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        stream = _load_wiki(path, spec.get("window"))
    elif fmt == "old_real_streams":
        stream = _load_old_real_streams()[spec["key"]]
        window = spec.get("window")
        if window is not None:
            stream = stream[:window]
    else:
        raise ValueError(f"unknown stream format {fmt!r}")
    _CACHE[cache_key] = stream
    return stream


def stream_summary(stream: list[str]) -> dict:
    counts: dict[str, int] = {}
    for name in stream:
        counts[name] = counts.get(name, 0) + 1
    n = len(stream)
    k = len(counts)
    singletons = sum(1 for v in counts.values() if v == 1)
    top = max(counts.values()) if counts else 0
    return {"n_arrivals": n, "families": k, "singleton_families": singletons,
            "singleton_family_pct": 100.0 * singletons / max(1, k),
            "top_family_n": top,
            "top_family_share_pct": 100.0 * top / max(1, n)}

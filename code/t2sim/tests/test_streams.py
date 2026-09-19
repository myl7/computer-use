"""Unit tests of the stream loaders (t2sim/streams.py)."""
from __future__ import annotations

import json
import random

import pytest

import streams as streams_mod


FAMS = [f"F{i}" for i in range(5)]


@pytest.mark.parametrize("pattern", streams_mod.ALL_PATTERNS)
def test_gen_stream_shapes_and_determinism(pattern):
    a = streams_mod.gen_stream(pattern, 500, FAMS, random.Random(3))
    b = streams_mod.gen_stream(pattern, 500, FAMS, random.Random(3))
    c = streams_mod.gen_stream(pattern, 500, FAMS, random.Random(4))
    assert a == b                       # same seed -> same stream
    assert a != c                       # different seed -> different stream
    assert len(a) == 500
    assert set(a) <= set(FAMS)
    s = streams_mod.stream_summary(a)
    assert s["n_arrivals"] == 500


def test_gen_stream_regime_shift_has_a_hot_then_cold_family():
    stream = streams_mod.gen_stream("regime_shift", 2000, FAMS,
                                    random.Random(1))
    first = stream[:1000].count(FAMS[0])
    after = stream[1000:].count(FAMS[0])
    assert first > 700                  # hot in the first half
    assert after < 120                  # trickling at ~3% afterwards


def test_wiki_loader_sorts_by_ts_and_windows(tmp_path):
    # deliberately out of chronological order
    rows = [("2026-09-03T00:00:09Z", "B"),
            ("2026-09-03T00:00:01Z", "A"),
            ("2026-09-03T00:00:05Z", "C"),
            ("2026-09-03T00:00:03Z", "B")]
    p = tmp_path / "tiny.jsonl"
    p.write_text("\n".join(json.dumps({"ts": t, "family_id": f})
                           for t, f in rows) + "\n")
    spec = {"format": "wiki_jsonl", "path": str(p)}
    s = streams_mod.load_stream(spec)
    assert s == ["A", "B", "C", "B"]     # arrival order = ts order
    s2 = streams_mod.load_stream({**spec, "window": 2})
    assert s2 == ["A", "B"]
    # in-process cache: the windowed variant is a different cache key
    assert streams_mod.load_stream(spec) == s


def test_wiki_loader_accepts_repo_relative_paths():
    spec = {"format": "wiki_jsonl",
            "path": "datasets/wiki-stream/stream_tool_hot_tail.jsonl",
            "window": 25}
    s = streams_mod.load_stream(spec)
    assert len(s) == 25
    assert all(isinstance(x, str) for x in s)


def test_old_real_streams_loader_and_window():
    # these are the frozen public logs; only the loader contract is tested
    spec = {"format": "old_real_streams", "key": "sepsis"}
    s = streams_mod.load_stream(spec)
    assert len(s) == 1050
    sw = streams_mod.load_stream({**spec, "window": 40})
    assert sw == s[:40]                  # window = first N arrivals
    with pytest.raises(KeyError):
        streams_mod.load_stream({**spec, "key": "no_such_log"})
    with pytest.raises(ValueError):
        streams_mod.load_stream({"format": "parquet"})


def test_stream_summary_fields():
    s = ["A", "A", "A", "B", "C"]
    summary = streams_mod.stream_summary(s)
    assert summary["n_arrivals"] == 5
    assert summary["families"] == 3
    assert summary["singleton_families"] == 2
    assert summary["singleton_family_pct"] == pytest.approx(200.0 / 3)
    assert summary["top_family_n"] == 3
    assert summary["top_family_share_pct"] == pytest.approx(60.0)
    assert streams_mod.stream_summary([])["n_arrivals"] == 0

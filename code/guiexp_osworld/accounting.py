"""Usage records, the price-weighted unit, the cold-host imputation, the floor.

Everything the new pipeline needs from day one (the brief's rulings):

* every call records prompt/completion/cached tokens and cost_usd, verbatim
  (``call_record``; ``cached_tokens=None`` means "not reported");
* the unit is PRICE-WEIGHTED: fresh + r_c x cached + r_o x completion, with
  the per-model cache-read and output price ratios r_c, r_o;
* the COLD-HOST imputation (docs/cache-adjusted-accounting.md 8.1, the new
  pipeline implements it from day one): inside one episode's call sequence,
  a call whose prefix should hit the provider cache but reports cached=0 is
  imputed the cache share the equivalent warm call has -- the previous call's
  prompt, when this call's prompt grew past it. Single calls (builder,
  extraction, reflection, analyzer) stay fresh in full.
* the FLOOR: the mean price-weighted cost of the 18 no-task probe runs,
  subtracted from every agent-side and document-side episode cost (never from
  compile or extraction).
"""

from __future__ import annotations

# r_c = cache-read price / input price; r_o = output price / input price.
# The brief's values. docs/cache-adjusted-accounting.md records 0.318/30.0 for
# the DS tiers of 2026-09-05; prices are re-verified live in the trial stage
# and any correction is recorded in the port log before full measurement.
PRICE_WEIGHTS = {
    "z-ai/glm-5.3-flash": {"r_c": 0.20, "r_o": 3.33},
    "deepseek/deepseek-v4-flash-vision-exp": {"r_c": 0.032, "r_o": 3.00},
    "qwen/qwen3.8-flash": {"r_c": 0.107, "r_o": 3.13},
    "mock": {"r_c": 0.20, "r_o": 3.33},  # tests only
}

FLOOR_RUNS = 18  # the brief: 18 no-task empty runs, averaged


def weights_for(model: str) -> dict:
    if model not in PRICE_WEIGHTS:
        raise KeyError(f"no price weights recorded for {model!r}")
    return PRICE_WEIGHTS[model]


def call_record(step: int, call: int, usage: dict, stage: str | None = None, **extra) -> dict:
    """One model call's usage in the shape every stage persists (Android arm's
    accounting_check.call_record, unchanged)."""
    usage = usage or {}
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    record = {
        "step": step,
        "call": call,
        "first_call": call == 1,
        "prompt_tokens": prompt,
        "cached_tokens": usage.get("cached_tokens"),
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": usage.get("cost_usd"),
        "usage": dict(usage),
    }
    if stage:
        record["stage"] = stage
    record.update(extra)
    return record


def sum_usage(usages) -> dict:
    out = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
           "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    for usage in usages:
        out["calls"] += 1
        out["prompt_tokens"] += usage.get("prompt_tokens") or 0
        out["completion_tokens"] += usage.get("completion_tokens") or 0
        out["cached_tokens"] += usage.get("cached_tokens") or 0
        out["cost_usd"] = round(out["cost_usd"] + (usage.get("cost_usd") or 0.0), 8)
    out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out


def zero() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


def add(totals: dict, usage: dict, calls: int = 1) -> dict:
    totals["calls"] += calls
    totals["prompt_tokens"] += usage.get("prompt_tokens") or 0
    totals["cached_tokens"] += usage.get("cached_tokens") or 0
    totals["completion_tokens"] += usage.get("completion_tokens") or 0
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost_usd"] = round(totals["cost_usd"] + (usage.get("cost_usd") or 0.0), 8)
    return totals


def merge(*totals: dict) -> dict:
    out = zero()
    for item in totals:
        out["calls"] += item.get("calls", 0)
        out["prompt_tokens"] += item.get("prompt_tokens", 0)
        out["cached_tokens"] += item.get("cached_tokens", 0)
        out["completion_tokens"] += item.get("completion_tokens", 0)
        out["cost_usd"] = round(out["cost_usd"] + item.get("cost_usd", 0.0), 8)
    out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out


# ----------------------------------------------------------- the priced unit


def price_weighted_call(prompt: int, cached: int | None, completion: int,
                        r_c: float, r_o: float) -> int:
    """One call in the price-weighted unit (cached=None counts as 0)."""
    cached = cached or 0
    fresh = max(0, prompt - cached)
    return int(round(fresh + r_c * cached + r_o * completion))


def price_weighted_totals(totals: dict, r_c: float, r_o: float) -> int:
    cached = totals.get("cached_tokens") or 0
    fresh = max(0, (totals.get("prompt_tokens") or 0) - cached)
    return int(round(fresh + r_c * cached + r_o * (totals.get("completion_tokens") or 0)))


def cache_adjusted(totals: dict, r: float) -> int:
    """The OLD cache-discounted unit (fresh + r x cached + completion), kept so
    the two accountings can be reported side by side like the Android arm."""
    cached = totals.get("cached_tokens") or 0
    fresh = (totals.get("prompt_tokens") or 0) - cached
    return int(round(fresh + r * cached + (totals.get("completion_tokens") or 0)))


# ------------------------------------------------------- cold-host imputation


def effective_call_tokens(record: dict, previous_prompt: int | None,
                          r_c: float, r_o: float) -> dict:
    """One call in the priced unit AFTER the cold-host imputation.

    ``record`` is a call_record-shaped dict (needs prompt_tokens,
    cached_tokens, completion_tokens). ``previous_prompt`` is the previous
    call's prompt size inside the SAME episode (None for the first call and
    for single-call stages).

    Rule (the brief's ruling; docs/cache-adjusted-accounting.md 8.1): the
    cache share actually hit (reported cached) is compared against the prefix
    that SHOULD hit (the previous call's prompt, when this prompt grew past
    it); the larger of the two is taken. A cold call (cached=0) on a warm-
    deserving prefix is therefore priced at the warm rate.
    """
    prompt = record.get("prompt_tokens") or 0
    completion = record.get("completion_tokens") or 0
    reported = record.get("cached_tokens") or 0
    imputed = 0
    if previous_prompt and prompt >= previous_prompt > 0:
        imputed = previous_prompt
    effective_cached = max(reported, imputed)
    fresh = max(0, prompt - effective_cached)
    unit = int(round(fresh + r_c * effective_cached + r_o * completion))
    return {
        "unit_tokens": unit,
        "effective_cached": effective_cached,
        "reported_cached": reported,
        "imputed_cached": max(0, effective_cached - reported),
    }


def episode_unit(calls: list[dict], r_c: float, r_o: float,
                 floor_unit: int | None = None) -> dict:
    """A whole episode's calls (call_record shapes, in order) -> priced unit
    totals with the cold-host imputation applied call by call.

    ``floor_unit`` (the measured floor, a priced-unit constant per episode) is
    subtracted at the end when given; never negative.
    """
    unit = 0
    effective_cached = 0
    imputed = 0
    previous_prompt: int | None = None
    for record in calls:
        result = effective_call_tokens(record, previous_prompt, r_c, r_o)
        unit += result["unit_tokens"]
        effective_cached += result["effective_cached"]
        imputed += result["imputed_cached"]
        previous_prompt = record.get("prompt_tokens") or 0
    if floor_unit:
        unit = max(0, unit - floor_unit)
    return {
        "unit_tokens": unit,
        "calls": len(calls),
        "effective_cached": effective_cached,
        "imputed_cached": imputed,
        "floor_subtracted": floor_unit or 0,
    }


def floored_episode_cost(calls: list[dict], floor_unit: int,
                         r_c: float, r_o: float) -> int:
    """episode_unit with the floor subtracted (the agent/doc-side costing)."""
    return episode_unit(calls, r_c, r_o, floor_unit=floor_unit)["unit_tokens"]

"""Offline extractor for the build-protocol constants table (t16_build).

Reads the finished build cells under

    experimental-results/guiexp_android/t16_build/<model_slug>/<Family>/

(any directory whose name contains ``.spoiled-`` is skipped) and writes the
per-cell constants the paper needs, plus per-model aggregates.  The extractor
spends no tokens and touches no device: it only reads ``build.json`` and the
stage files beside it.

Headline accounting: price-weighted tokens (``pw``)
--------------------------------------------------
Every model call is charged

    tokens_pw = (prompt_tokens - cached_tokens)
                + r_c * cached_tokens
                + r_o * completion_tokens

with r_c = p_c / p_in and r_o = p_o / p_in from ``PRICE_SHEET`` below, the
OpenRouter list prices fetched 2026-09-11 (``PRICE_SHEET_FETCHED``): GLM
r_c 0.20 / r_o 3.33 and DeepSeek r_c 0.0318 / r_o 3.0.  The sheet in
``docs/cache-adjusted-accounting.md`` section 1 is stale -- GLM's three prices
were low by exactly 2x (ratios unchanged) and DeepSeek's p_in was low by 10x,
which is where its 0.318 and 30 came from.

``cached_tokens`` is the provider-reported field (OpenRouter
``usage.prompt_tokens_details.cached_tokens``); a missing or null one is read
as 0 and counted in ``cached_tokens_missing_calls``.  The formula is linear in
the three counts, so it applies to a stage total exactly as it applies to a
call, and every stage with a prompt/cached/completion split is computable, the
verification resume episodes included.

A per-call regression showed the recorded bill equals this formula call by
call up to a discrete OpenRouter provider-routing factor (some GLM calls
billed at 0.5x, some DeepSeek calls at 2x).  That factor is noise for token
efficiency, which is why the headline is the formula and the bill is secondary.

Secondary units, kept for the appendix
--------------------------------------
* ``usd_over_p_in``: the recorded ``cost_usd`` divided by the model's
  fresh-input list price, i.e. the bill itself in the same dimension.  Each
  stage carries ``ratio_pw_over_usd`` in ``counts_diagnostic.per_stage``:
  1 where the bill follows the sheet, and roughly 0.5 to 3.2 where routing or
  image surcharges moved it.  The deployment stage of the old cells has no
  prompt/cached/completion split, so ``usd_over_p_in`` is the only unit
  available there and d is taken from it (one uncached extraction call, so the
  two agree up to the routing factor).
* raw: the recorded ``prompt_tokens + completion_tokens``.
* ``cache_adjusted_deterministic``: the per-call fold of
  ``docs/cache-adjusted-accounting.md`` section 8, which ignores the
  provider's ``cached_tokens`` and infers the reused prefix from the growth of
  the prompt inside one episode.  It needs per-call records, so it stays
  undefined for the verification resume episodes, exactly as before.
* the per-success readings of c and of the break-even point (M1's appendix).
* ``admitted_5of5`` and ``admitted_protocol`` next to the headline 4-of-5
  admission rule.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable

# Admission (M2): the gate's held-out bindings and the pass threshold, owned by
# gate_runner.py so the extractor and the protocol cannot drift apart.
from .gate_runner import GATE_K, GATE_MIN_PASS

# OpenRouter list prices, https://openrouter.ai/api/v1/models, fetched
# 2026-09-11.  These supersede the sheet in docs/cache-adjusted-accounting.md
# section 1, which is stale: GLM's three prices were low by exactly 2x (its
# ratios are unchanged) and DeepSeek's p_in was low by 10x, which makes
# r_c = 0.0318 and r_o = 3.0 there, not 0.318 and 30.  p_in is the headline
# unit's divisor; p_c and p_o only feed the counts-based secondary unit.
PRICE_SHEET_FETCHED = "2026-09-11"
PRICE_SHEET_SOURCE = "https://openrouter.ai/api/v1/models"
PRICE_SHEET = {
    "z-ai/glm-5.3-flash": {"p_in": 1.5e-7, "p_c": 3e-8, "p_o": 5e-7},
    "deepseek/deepseek-v4-flash-vision-exp": {"p_in": 2.2e-7, "p_c": 7e-9, "p_o": 6.6e-7},
    "qwen/qwen3.8-flash": {"p_in": 1.5e-7, "p_c": 1.6e-8, "p_o": 4.7e-7},
}

# Per-model global floor, in RAW tokens: the tokens an episode spends before it
# does anything task-specific.  Measured on 18 no-task episodes, dispersion
# under 0.5%.  The floor is a prompt that was sent uncached and produced no
# completion, so the price-weighted formula's coefficient on it is 1 and the
# floor in the headline unit is the same number; see floor_pw().
FLOOR_RAW_TOKENS = {
    "z-ai/glm-5.3-flash": 5090,
    "deepseek/deepseek-v4-flash-vision-exp": 2290,
}

# The k the protocol deploys.  A cell declared unautomatable is still
# admissible if this k's INITIAL gate cleared the threshold, which means the
# repair loop discarded a program that would have been admitted.
PROTOCOL_K = 3

# r of the deterministic secondary unit, docs/cache-adjusted-accounting.md
# section 8 and guiexp_android/build_protocol.py CACHE_RATIO.
CACHE_RATIO = {
    "z-ai/glm-5.3-flash": 0.20,
    "deepseek/deepseek-v4-flash-vision-exp": 0.318,
}

DEFAULT_ROOT = Path("../experimental-results/guiexp_android/t16_build")
INF = "inf"
CROSS_CHECK_BAND = (0.5, 2.0)

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- primitives


def _num(value: Any) -> float | None:
    """A finite number, or None for anything that is not one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator, None if either side is missing or zero below."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def nstar(numerator: float | str | None, saving: float | None) -> float | str | None:
    """Break-even uses.  "inf" when the numerator is infinite or s <= 0."""
    if numerator == INF:
        return INF
    if numerator is None or saving is None:
        return None
    if saving <= 0:
        return INF
    return numerator / saving


def c_eff(build_cost: float | None, p: float | None) -> float | str | None:
    """SECONDARY: C / p.  The old reading, kept as *_c_over_p."""
    if build_cost is None or p is None:
        return None
    if p <= 0:
        return INF
    return build_cost / p


def effective_price(
    build_cost: float | None, p: float | None, fail_cost: float | None
) -> float | str | None:
    """HEADLINE: C_eff = C + (1/p - 1) * C_fail.

    A compile attempt that the gate rejects is not free and is not priced like
    the one that succeeded, so the expected 1/p - 1 failed attempts are charged
    at their own price.  ``fail_cost`` is the model's median C_with_repair over
    its not-admitted cells: a cell admitted on the first attempt never observed
    a failure of its own.  p = 1 gives C, p = 0 gives "inf".
    """
    if build_cost is None or p is None:
        return None
    if p <= 0:
        return INF
    if p >= 1:
        return build_cost
    if fail_cost is None:
        return None
    return build_cost + (1.0 / p - 1.0) * fail_cost


def add_opt(*values: float | None) -> float | None:
    """Sum that propagates a missing operand as None."""
    out = 0.0
    for value in values:
        if value is None:
            return None
        out += value
    return out


def sub_opt(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def raw_tokens(totals: dict | None) -> int | None:
    if not totals:
        return None
    if totals.get("total_tokens") is not None:
        return int(totals["total_tokens"])
    prompt = totals.get("prompt_tokens")
    completion = totals.get("completion_tokens")
    if prompt is None and completion is None:
        return None
    return int((prompt or 0) + (completion or 0))


def cost_of(totals: dict | None) -> float:
    if not totals:
        return 0.0
    return float(totals.get("cost_usd") or 0.0)


# ------------------------------------------------------- the billed-equiv unit


class Meter:
    """The price-weighted headline unit and the bill-based secondary, per model.

    One instance per cell accumulates the per-stage diagnostic and the count of
    calls whose ``cached_tokens`` the provider did not report.
    """

    def __init__(self, model: str | None):
        prices = PRICE_SHEET.get(model or "")
        self.model = model
        self.prices = prices
        self.p_in = prices["p_in"] if prices else None
        self.r_c = prices["p_c"] / prices["p_in"] if prices else None
        self.r_o = prices["p_o"] / prices["p_in"] if prices else None
        self.missing_cached_calls = 0
        self.stages: dict[str, dict[str, Any]] = {}

    # -- one usage record ---------------------------------------------------

    def usd(self, totals: dict | None) -> float | None:
        """SECONDARY: recorded cost_usd / p_in, the bill in the same dimension."""
        if not totals or self.p_in is None:
            return None
        cost = _num(totals.get("cost_usd"))
        if cost is None:
            return None
        return cost / self.p_in

    def counts(self, totals: dict | None, *, calls: int | None = None) -> float | None:
        """HEADLINE: (prompt - cached) + r_c * cached + r_o * completion."""
        if not totals or self.r_c is None or self.r_o is None:
            return None
        prompt = _num(totals.get("prompt_tokens"))
        completion = _num(totals.get("completion_tokens"))
        if prompt is None and completion is None:
            return None
        cached = _num(totals.get("cached_tokens"))
        if cached is None:
            self.missing_cached_calls += (
                calls
                if calls is not None
                else int(totals.get("calls") or totals.get("model_calls") or 1)
            )
            cached = 0.0
        prompt = prompt or 0.0
        completion = completion or 0.0
        fresh = prompt - cached
        return fresh + self.r_c * cached + self.r_o * completion

    def measure(
        self, totals: dict | None, *, name: str | None = None, calls: int | None = None
    ) -> tuple[float | None, float | None]:
        """(bill, price-weighted) of one record, filed under ``name`` if given."""
        bill = self.usd(totals)
        weighted = self.counts(totals, calls=calls)
        if name is not None:
            self.stages[name] = {
                "pw": weighted,
                "usd_over_p_in": bill,
                "ratio_pw_over_usd": ratio(weighted, bill),
            }
        return bill, weighted

    def stage(self, name: str, totals: dict | None) -> float | None:
        """Meter one stage total; returns the BILL (index 1 is the headline)."""
        return self.measure(totals, name=name)[0]

    def diagnostic(self) -> dict:
        """Price-weighted vs bill, per stage and summed over the cell.

        The ratio is 1 where the provider billed at the list sheet; it moves by
        the discrete routing factor otherwise.  A diagnostic, not a correction.
        """
        # Only stages where both units are defined, so the cell ratio compares
        # like with like (the deployment stage has no counts reading).
        both = [
            s
            for s in self.stages.values()
            if s["usd_over_p_in"] is not None and s["pw"] is not None
        ]
        total_headline = sum(s["usd_over_p_in"] for s in both) if both else None
        total_counted = sum(s["pw"] for s in both) if both else None
        cell_ratio = ratio(total_counted, total_headline)
        low, high = CROSS_CHECK_BAND
        return {
            "pw_total": total_counted,
            "usd_over_p_in_total": total_headline,
            "ratio_pw_over_usd": cell_ratio,
            "band": [low, high],
            "outside_band": cell_ratio is not None and not (low <= cell_ratio <= high),
            "per_stage": self.stages,
        }


def floor_pw(model: str | None) -> float | None:
    """The per-episode floor in the headline unit.

    The floor was measured as raw tokens on no-task episodes.  It is charged as
    UNCACHED PROMPT (cached = 0, completion = 0), whose coefficient in the
    price-weighted formula is 1, so the floor in the headline unit is the same
    number as the raw floor.  The assumption is written out as a usage record
    rather than left implicit.
    """
    raw = FLOOR_RAW_TOKENS.get(model or "")
    if raw is None:
        return None
    meter = Meter(model)
    return meter.counts(
        {"prompt_tokens": raw, "cached_tokens": 0, "completion_tokens": 0}, calls=0
    )


# ---------------------------------- deterministic accounting (secondary unit)


def episode_effective_tokens(calls: list[tuple[int, int]], r: float) -> dict:
    """Deterministic cache-adjusted tokens of one episode.

    ``calls`` is the episode's per-call ``(prompt_tokens, completion_tokens)``
    in call order.  Returns the effective total plus the edge-case counts of
    docs/cache-adjusted-accounting.md section 8.3.  Secondary unit: the
    headline is the billed-equivalent one above.
    """
    effective = 0.0
    shrunk = 0
    repeated = 0
    previous_prompt: int | None = None
    for prompt, completion in calls:
        if previous_prompt is None:
            effective += prompt + completion
        elif prompt < previous_prompt:
            # Truncation or summarisation: the prefix is gone, charge fresh.
            effective += prompt + completion
            shrunk += 1
        elif prompt == previous_prompt:
            effective += r * prompt + completion
            repeated += 1
        else:
            effective += (prompt - previous_prompt) + r * previous_prompt + completion
        previous_prompt = prompt
    return {
        "effective_tokens": effective,
        "calls": len(calls),
        "prompt_shrank_calls": shrunk,
        "prompt_repeated_calls": repeated,
    }


def trajectory_calls(path: Path) -> list[tuple[int, int]] | None:
    """Per-call (prompt, completion) of a trajectory.jsonl, or None if absent.

    The final summary line of a trajectory carries no ``usage`` and is skipped.
    """
    if not path.exists():
        return None
    calls: list[tuple[int, int]] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            usage = record.get("usage")
            if not usage:
                continue
            calls.append(
                (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
            )
    return calls


def resolve_trajectory(raw_path: str, cell_dir: Path) -> Path:
    """Trajectory paths in the records are absolute, or relative to computer-use/."""
    path = Path(raw_path)
    if path.is_absolute():
        return path
    for base in (REPO_ROOT / "computer-use", cell_dir, Path.cwd()):
        candidate = (base / path).resolve()
        if candidate.exists():
            return candidate
    return (REPO_ROOT / "computer-use" / path).resolve()


# ----------------------------------------------------------- stage readers


def fresh_cache_sums(build: dict) -> dict[str, float]:
    """A cell's own evidence of its cache share, per source.

    The cell's NON-reused exploration attempts and its doc-arm episodes: same
    agent, same family, same prompt structure, and the doc arm's totals carry
    cached_tokens.  Keeping both means a single cold exploration episode that
    reports 0 does not decide the share on its own.
    """
    episodes = ((build.get("exploration") or {}).get("per_episode")) or []
    fresh_cached = sum(
        float(e.get("cached_tokens") or 0) for e in episodes if not e.get("reused")
    )
    fresh_prompt = sum(
        float(e.get("prompt_tokens") or 0) for e in episodes if not e.get("reused")
    )
    doc = ((build.get("doc_arm") or {}).get("totals")) or {}
    doc_cached = float(doc.get("cached_tokens") or 0)
    doc_prompt = float(doc.get("prompt_tokens") or 0)
    return {
        "fresh_cached": fresh_cached,
        "fresh_prompt": fresh_prompt,
        "doc_cached": doc_cached,
        "doc_prompt": doc_prompt,
        "cached": fresh_cached + doc_cached,
        "prompt": fresh_prompt + doc_prompt,
    }


def impute_reused(episode: dict, share: float | None) -> tuple[dict, float]:
    """A reused episode with its cached_tokens imputed, and the amount imputed.

    Exploration reused t12_grid episodes for some seeds.  Those trajectory
    records carry prompt_tokens, completion_tokens and cost_usd only -- no
    cached_tokens -- so the price-weighted formula would charge their whole
    prompt as fresh and overstate c.  The cache share of the SAME cell's fresh
    exploration attempts is applied to their prompt instead.
    """
    if not episode.get("reused") or share is None:
        return episode, 0.0
    if _num(episode.get("cached_tokens")):  # a real, non-zero report: keep it
        return episode, 0.0
    prompt = _num(episode.get("prompt_tokens")) or 0.0
    imputed = prompt * share
    return {**episode, "cached_tokens": imputed}, imputed


def read_exploration(
    cell_dir: Path,
    build: dict,
    meter: Meter,
    r: float | None,
    model_cache_share: float | None = None,
) -> dict:
    """Exploration attempts, successes, and the three c readings.

    An ATTEMPT is one exploration episode: the first episode of an instance
    plus its reflection retries (explore.py runs up to N_REF = 2 retries after
    a failure, each preceded by one reflection call, and appends every one of
    them to ``per_episode``).  A reflection call is part of the cost of the
    reactive attempt it enables, so the headline numerator is the stage total
    ``exploration.totals``, which explore.py's ``summarise`` builds from the
    episode usages AND the reflection usages.  ``c_attempt_episodes_only``
    keeps the numerator without the reflection calls, as a secondary reading.
    """
    exploration = build.get("exploration") or {}
    episodes = list(exploration.get("per_episode") or [])
    attempts = len(episodes)
    successes = sum(1 for e in episodes if e.get("success"))
    totals = exploration.get("totals") or {}

    episode_raw = [raw_tokens(e) for e in episodes]
    episodes_raw_total = add_opt(*episode_raw) if episode_raw else 0.0
    total_raw = raw_tokens(totals)
    reflections = list(exploration.get("reflections") or [])
    reflections_raw = add_opt(*[raw_tokens(x) for x in reflections]) if reflections else 0.0

    # ---- cache imputation for the episodes reused from t12_grid.
    evidence = fresh_cache_sums(build)
    if evidence["prompt"]:
        cache_share = evidence["cached"] / evidence["prompt"]
        share_source = {
            (True, True): "cell fresh+doc",
            (True, False): "cell fresh",
            (False, True): "cell doc",
        }[(bool(evidence["fresh_prompt"]), bool(evidence["doc_prompt"]))]
    else:
        cache_share = model_cache_share
        share_source = "model mean"
    imputed_episodes = []
    imputed_total = 0.0
    imputed_count = 0
    for episode in episodes:
        adjusted, amount = impute_reused(episode, cache_share)
        imputed_episodes.append(adjusted)
        imputed_total += amount
        imputed_count += 1 if amount else 0
    # exploration.totals already sums those prompts with cached 0, so the
    # imputed cache is added to the stage total's cached count once, here.
    imputed_totals = dict(totals)
    if imputed_total:
        imputed_totals["cached_tokens"] = (_num(totals.get("cached_tokens")) or 0.0) + imputed_total

    # Price-weighted (headline) and the bill, from the recorded sums.
    total_usd_over_p_in, total_counts = meter.measure(imputed_totals, name="exploration")
    total_counts_no_imputation = meter.counts(totals, calls=0)
    measured = [meter.measure(e) for e in imputed_episodes]
    episode_usd_over_p_in = [m[0] for m in measured]
    episode_counts = [m[1] for m in measured]
    episodes_usd_over_p_in_total = (
        add_opt(*episode_usd_over_p_in) if episode_usd_over_p_in else 0.0
    )
    episodes_pw_total = add_opt(*episode_counts) if episode_counts else 0.0
    reflections_measured = [meter.measure(x, calls=1) for x in reflections]
    reflections_usd_over_p_in = (
        add_opt(*[m[0] for m in reflections_measured]) if reflections_measured else 0.0
    )

    # Deterministic secondary unit, per episode, from the recorded trajectories.
    per_episode_ca: list[float | None] = []
    edge_cases = {"prompt_shrank_calls": 0, "prompt_repeated_calls": 0}
    missing_trajectories: list[str] = []
    mismatches: list[str] = []
    index = {}
    exploration_json = cell_dir / "explore" / "exploration.json"
    if exploration_json.exists():
        detail = json.loads(exploration_json.read_text())
        for instance in detail.get("instances") or []:
            for attempt in instance.get("attempts") or []:
                index[(instance.get("seed"), attempt.get("attempt"))] = attempt.get("trajectory")
    for episode in episodes:
        key = (episode.get("seed"), episode.get("attempt"))
        raw_path = index.get(key)
        calls = None
        if raw_path:
            calls = trajectory_calls(resolve_trajectory(raw_path, cell_dir))
        if calls is None:
            fallback = (
                cell_dir
                / "explore"
                / f"s{episode.get('seed')}_a{episode.get('attempt')}"
                / "trajectory.jsonl"
            )
            calls = trajectory_calls(fallback)
        if calls is None:
            per_episode_ca.append(None)
            missing_trajectories.append(f"s{episode.get('seed')}_a{episode.get('attempt')}")
            continue
        result = episode_effective_tokens(calls, r) if r is not None else None
        if result is None:
            per_episode_ca.append(None)
            continue
        per_episode_ca.append(result["effective_tokens"])
        edge_cases["prompt_shrank_calls"] += result["prompt_shrank_calls"]
        edge_cases["prompt_repeated_calls"] += result["prompt_repeated_calls"]
        trajectory_raw = sum(p + c for p, c in calls)
        if raw_tokens(episode) is not None and trajectory_raw != raw_tokens(episode):
            mismatches.append(
                f"s{episode.get('seed')}_a{episode.get('attempt')}: trajectory sums to "
                f"{trajectory_raw}, build.json records {raw_tokens(episode)}"
            )

    ca_available = bool(per_episode_ca) and all(x is not None for x in per_episode_ca)
    episodes_ca_total = sum(per_episode_ca) if ca_available else None
    total_ca = add_opt(episodes_ca_total, reflections_raw) if ca_available else None

    success_raw = [raw_tokens(e) for e in episodes if e.get("success")]
    success_usd_over_p_in = [b for b, e in zip(episode_usd_over_p_in, episodes) if e.get("success")]
    success_counts = [b for b, e in zip(episode_counts, episodes) if e.get("success")]
    success_ca = (
        [x for x, e in zip(per_episode_ca, episodes) if e.get("success")] if ca_available else None
    )

    # A totals record that does not equal the sum of its parts is reported.
    parts_usd_over_p_in = add_opt(episodes_usd_over_p_in_total, reflections_usd_over_p_in)
    totals_gap = None
    if total_usd_over_p_in is not None and parts_usd_over_p_in is not None and total_usd_over_p_in:
        totals_gap = (parts_usd_over_p_in - total_usd_over_p_in) / total_usd_over_p_in

    return {
        "attempts": attempts,
        "successes": successes,
        "pi": ratio(successes, attempts),
        "retry_episodes": exploration.get("retry_episodes"),
        "reused_episodes": exploration.get("reused_episodes"),
        "episodes": [
            {
                "seed": e.get("seed"),
                "attempt": e.get("attempt"),
                "reused": bool(e.get("reused")),
                "success": bool(e.get("success")),
                "steps": e.get("steps"),
                "raw_tokens": raw_tokens(e),
                "pw_tokens": bc,
                "usd_over_p_in_tokens": b,
                "cache_adjusted_deterministic_tokens": ca,
            }
            for e, b, bc, ca in zip(episodes, episode_usd_over_p_in, episode_counts, per_episode_ca)
        ],
        "reflection_calls": len(reflections),
        "reflection_tokens_raw": reflections_raw,
        "reflection_tokens_usd_over_p_in": reflections_usd_over_p_in,
        "tokens_total_usd_over_p_in": total_usd_over_p_in,
        "tokens_total_pw": total_counts,
        "tokens_episodes_usd_over_p_in": episodes_usd_over_p_in_total,
        "tokens_total_raw": total_raw,
        "tokens_episodes_raw": episodes_raw_total,
        "tokens_episodes_cache_adjusted_deterministic": episodes_ca_total,
        "tokens_total_cache_adjusted_deterministic": total_ca,
        # headline: every attempt, reflection calls folded into the numerator
        "c_attempt_usd_over_p_in": ratio(total_usd_over_p_in, attempts),
        "c_attempt_usd_over_p_in_episodes_only": ratio(episodes_usd_over_p_in_total, attempts),
        "c_success_usd_over_p_in": ratio(
            add_opt(*success_usd_over_p_in) if success_usd_over_p_in else None, len(success_usd_over_p_in) or None
        ),
        "c_per_delivery_usd_over_p_in": ratio(total_usd_over_p_in, successes),
        "reused_episodes_cache_imputed": imputed_count,
        "reused_cache_share_used": cache_share,
        "reused_cache_share_source": share_source if imputed_count else None,
        "reused_cache_share_evidence": evidence,
        "reused_cache_tokens_imputed": imputed_total,
        "reused_episodes_usd_over_p_in": add_opt(
            *[b for b, e in zip(episode_usd_over_p_in, episodes) if e.get("reused")]
        ) if any(e.get("reused") for e in episodes) else 0.0,
        "c_attempt_pw": ratio(total_counts, attempts),
        "c_attempt_pw_no_imputation": ratio(total_counts_no_imputation, attempts),
        "tokens_total_pw_no_imputation": total_counts_no_imputation,
        "c_attempt_pw_episodes_only": ratio(episodes_pw_total, attempts),
        "tokens_episodes_pw": episodes_pw_total,
        "c_success_pw": ratio(
            add_opt(*success_counts) if success_counts else None, len(success_counts) or None
        ),
        "c_per_delivery_pw": ratio(total_counts, successes),
        "c_attempt_raw": ratio(total_raw, attempts),
        "c_success_raw": ratio(
            add_opt(*success_raw) if success_raw else None, len(success_raw) or None
        ),
        "c_per_delivery_raw": ratio(total_raw, successes),
        "c_attempt_cache_adjusted_deterministic": ratio(total_ca, attempts),
        "c_success_cache_adjusted_deterministic": (
            ratio(sum(success_ca), len(success_ca)) if success_ca else None
        ),
        "c_per_delivery_cache_adjusted_deterministic": ratio(total_ca, successes),
        "cache_adjusted_deterministic_available": ca_available,
        "missing_trajectories": missing_trajectories,
        "trajectory_total_mismatches": mismatches,
        "totals_vs_parts_usd_over_p_in_gap": totals_gap,
        "edge_cases": edge_cases,
        "cost_usd": cost_of(totals),
    }


def read_doc_arm(cell_dir: Path, build: dict, meter: Meter, r: float | None) -> dict:
    doc = build.get("doc_arm") or {}
    episodes = list(doc.get("episodes") or [])
    per_episode_ca: list[float | None] = []
    missing: list[str] = []
    for episode in episodes:
        seed = episode.get("seed")
        calls = trajectory_calls(cell_dir / "doc_arm" / f"s{seed}" / "trajectory.jsonl")
        if calls is None or r is None:
            per_episode_ca.append(None)
            if calls is None:
                missing.append(f"doc_arm/s{seed}")
            continue
        per_episode_ca.append(episode_effective_tokens(calls, r)["effective_tokens"])
    ca_available = bool(per_episode_ca) and all(x is not None for x in per_episode_ca)
    totals = doc.get("totals") or {}
    total_usd_over_p_in, total_counts = meter.measure(totals, name="doc_arm")
    return {
        "episodes": len(episodes),
        "successes": doc.get("success_count"),
        "tokens_total_usd_over_p_in": total_usd_over_p_in,
        "tokens_total_raw": raw_tokens(totals),
        "tokens_total_cache_adjusted_deterministic": sum(per_episode_ca) if ca_available else None,
        "tokens_total_pw": total_counts,
        "L_doc_usd_over_p_in": ratio(total_usd_over_p_in, len(episodes)),
        "L_doc_pw": ratio(total_counts, len(episodes)),
        "L_doc_raw": ratio(raw_tokens(totals), len(episodes)),
        "L_doc_cache_adjusted_deterministic": (
            ratio(sum(per_episode_ca), len(episodes)) if ca_available else None
        ),
        "cache_adjusted_deterministic_available": ca_available,
        "missing_trajectories": missing,
        "cost_usd": cost_of(totals),
    }


def read_translator(cell_dir: Path, build: dict, meter: Meter) -> dict:
    """Translator: one independent call per effective action."""
    totals = (build.get("translator") or {}).get("totals") or {}
    raw = raw_tokens(totals)
    return {
        "tokens_usd_over_p_in": meter.measure(totals, name="translator")[0],
        "tokens_pw": meter.stages["translator"]["pw"],
        "tokens_raw": raw,
        "tokens_cache_adjusted_deterministic": raw,
        "calls": totals.get("calls"),
        "cost_usd": cost_of(totals),
        "single_calls": True,
    }


def read_builder(build: dict, meter: Meter) -> dict:
    """Builder arms.  Every builder call is a single call."""
    builder = build.get("builder") or {}
    all_arms = builder.get("totals_all_calls") or {}
    selected = builder.get("selected_arm") or {}
    with_repair = selected.get("initial_plus_refinements") or {}
    key = f"k{selected.get('k')}_{selected.get('artifact')}"
    initial = (builder.get("initial") or {}).get(key) or {}
    initial_usd_over_p_in, initial_counts = meter.measure(initial, calls=1)
    return {
        "selected_k": selected.get("k"),
        "selected_artifact": selected.get("artifact"),
        "all_arms_tokens_usd_over_p_in": meter.stage("builder_all_arms", all_arms),
        "all_arms_tokens_pw": meter.stages["builder_all_arms"][
            "pw"
        ],
        "all_arms_tokens_raw": raw_tokens(all_arms),
        "all_arms_cost_usd": cost_of(all_arms),
        "selected_initial_tokens_usd_over_p_in": initial_usd_over_p_in,
        "selected_initial_tokens_pw": initial_counts,
        "selected_initial_tokens_raw": raw_tokens(initial),
        "selected_initial_cost_usd": cost_of(initial),
        "selected_with_refinements_tokens_usd_over_p_in": meter.usd(with_repair),
        "selected_with_refinements_tokens_raw": raw_tokens(with_repair),
        "selected_with_refinements_cost_usd": cost_of(with_repair),
        "per_k_initial_tokens_usd_over_p_in": {
            name: meter.usd(entry)
            for name, entry in (builder.get("initial") or {}).items()
        },
        "per_k_initial_tokens_raw": {
            name: raw_tokens(entry) for name, entry in (builder.get("initial") or {}).items()
        },
        "single_calls": True,
    }


def read_verification(build: dict, meter: Meter) -> dict:
    """Verification: analyzer, builder refinements, and the resume episodes.

    All three carry prompt/cached/completion stage sums, so all three are
    computable in the billed unit.  Only the deterministic secondary unit
    still cannot see inside the resume episodes.
    """
    verification = build.get("verification") or {}
    analyzer = verification.get("analyzer") or {}
    resume = verification.get("resume_episodes") or {}
    refinements = verification.get("builder_refinements") or {}
    excl = verification.get("totals_excluding_refinements") or {}
    return {
        "refinements": verification.get("refinements"),
        "program_replays": verification.get("program_replays"),
        "unautomatable": bool(verification.get("unautomatable")),
        "analyzer_tokens_usd_over_p_in": meter.stage("verification_analyzer", analyzer),
        "analyzer_tokens_pw": meter.stages["verification_analyzer"][
            "pw"
        ],
        "analyzer_tokens_raw": raw_tokens(analyzer),
        "analyzer_tokens_cache_adjusted_deterministic": raw_tokens(analyzer),
        "analyzer_cost_usd": cost_of(analyzer),
        "resume_episodes_tokens_usd_over_p_in": meter.stage("verification_resume_episodes", resume),
        "resume_episodes_tokens_pw": meter.stages[
            "verification_resume_episodes"
        ]["pw"],
        "resume_episodes_tokens_raw": raw_tokens(resume),
        "resume_episodes_tokens_cache_adjusted_deterministic": None,
        "resume_episodes_calls": resume.get("calls"),
        "resume_episodes_cost_usd": cost_of(resume),
        "builder_refinements_tokens_usd_over_p_in": meter.stage(
            "verification_builder_refinements", refinements
        ),
        "builder_refinements_tokens_pw": meter.stages[
            "verification_builder_refinements"
        ]["pw"],
        "builder_refinements_tokens_raw": raw_tokens(refinements),
        "builder_refinements_tokens_cache_adjusted_deterministic": raw_tokens(refinements),
        "builder_refinements_cost_usd": cost_of(refinements),
        "tokens_excluding_refinements_raw": raw_tokens(excl),
        "cost_usd_excluding_refinements": cost_of(excl),
        "cost_usd_all": cost_of(analyzer) + cost_of(resume) + cost_of(refinements),
        "test_seed0_passed": (verification.get("test_seed0") or {}).get("passed"),
    }


def read_gate(build: dict, verification: dict) -> dict:
    """Admission (M2), headline rule: gate passed / GATE_K, admitted at
    GATE_MIN_PASS of GATE_K.

    Three sources, in this order.

    * ``build.json["redeploy"]``: the redeploy path re-gated a discarded
      artifact and, if it passed, deployed it.  It leaves
      ``verification.gate_after_repair`` null and ``verification.unautomatable``
      true, so its own gate is the admission evidence and q, d come from the
      ``deploy`` section it wrote.  ``admission_source`` is "redeploy".
    * a cell whose verification declared the type unautomatable has p = 0,
      UNLESS the INITIAL gate of the k the protocol deploys (PROTOCOL_K)
      already cleared the threshold.  That combination means the repair loop
      threw away a program the admission rule would have admitted; the cell is
      marked ``admission_pending_redeploy`` and carries p = gate_k3 / GATE_K,
      with q and d left empty until the redeploy run exists.
    * otherwise the post-repair gate.
    """
    gate = build.get("gate_per_k") or {}
    out: dict[str, Any] = {}
    for k in (1, 2, 3):
        entry = gate.get(f"k{k}") or {}
        passed = entry.get("bindings_passed")
        total = entry.get("bindings_total")
        out[f"p_initial_k{k}"] = ratio(passed, total)
        out[f"gate_k{k}_passed"] = passed
        out[f"gate_k{k}_total"] = total

    after = (build.get("verification") or {}).get("gate_after_repair")
    after_passed = after.get("bindings_passed") if after else None
    after_total = after.get("bindings_total") if after else None
    out["gate_after_repair_passed"] = after_passed
    out["gate_after_repair_total"] = after_total
    redeploy = build.get("redeploy") or {}
    redeploy_gate = redeploy.get("gate") or {}
    redeploy_passed = redeploy_gate.get("bindings_passed")
    redeploy_total = redeploy_gate.get("bindings_total")
    out["redeploy_present"] = bool(redeploy)
    out["redeploy_gate_passed"] = redeploy_passed
    out["redeploy_gate_total"] = redeploy_total
    out["redeploy_timestamp"] = redeploy.get("timestamp")
    out["redeploy_source_artifact"] = redeploy.get("source_artifact")

    out["p_after_repair"] = (
        ratio(redeploy_passed, redeploy_total) if redeploy else ratio(after_passed, after_total)
    )
    out["admitted_5of5"] = (
        redeploy_passed == redeploy_total
        if redeploy
        else (bool(after) and after_passed == after_total)
    )
    out["admitted_protocol"] = not verification["unautomatable"]

    protocol_passed = out[f"gate_k{PROTOCOL_K}_passed"]
    pending = (
        not redeploy
        and bool(verification["unautomatable"])
        and (protocol_passed or 0) >= GATE_MIN_PASS
    )
    out["admission_pending_redeploy"] = pending

    if redeploy:
        out["p_headline"] = ratio(redeploy_passed, GATE_K)
        out["p_headline_source"] = (
            f"redeploy gate ({redeploy_passed}/{redeploy_total}); the repair loop had discarded "
            "that artifact and verification.unautomatable stays true"
        )
        out["admitted_headline"] = (
            None if redeploy_passed is None else redeploy_passed >= GATE_MIN_PASS
        )
        out["admission_source"] = "redeploy"
    elif pending:
        out["p_headline"] = ratio(protocol_passed, GATE_K)
        out["p_headline_source"] = (
            f"initial gate at k={PROTOCOL_K} ({protocol_passed}/{GATE_K}); the repair loop "
            "discarded that program and a redeploy run is scheduled"
        )
        out["admitted_headline"] = None
        out["admission_source"] = f"initial_gate_k{PROTOCOL_K}_pending_redeploy"
    elif verification["unautomatable"]:
        out["p_headline"] = 0.0
        out["p_headline_source"] = "verification declared the type unautomatable"
        out["admitted_headline"] = False
        out["admission_source"] = "unautomatable"
    else:
        out["p_headline"] = ratio(after_passed, GATE_K)
        out["p_headline_source"] = "post-repair gate"
        out["admitted_headline"] = (
            None if after_passed is None else after_passed >= GATE_MIN_PASS
        )
        out["admission_source"] = "post_repair_gate"
    out["gate_margin"] = out["p_headline"]
    out["gate_min_pass"] = GATE_MIN_PASS
    return out


def read_deploy(cell_dir: Path, build: dict, meter: Meter, pending: bool) -> dict:
    """Deployment: q and d.

    The per-use records of these cells keep ``tokens`` and ``cost_usd`` only,
    with no prompt/cached/completion split (``calls_detail`` was added to
    deploy_runner.py after these runs), so the price-weighted formula cannot be
    applied here.  d is therefore taken from the bill, d = (cost_usd / p_in) /
    n: a deployment use is one uncached extraction call, for which
    cost / p_in = prompt + r_o * completion is the formula itself, up to the
    provider-routing factor.  ``secondary_units.raw.d`` keeps the recorded
    ``d_tokens_mean``.
    """
    deploy = build.get("deploy") or {}
    empty = {
        "deployed": False,
        "skipped": deploy.get("skipped"),
        "pending_redeploy": pending,
        "n": None,
        "successes": None,
        "success_rate": None,
        "q": None,
        "d_pw": None,
        "d_usd_over_p_in": None,
        "d_raw_tokens_mean": None,
        "tokens_total_raw": None,
        "tokens_total_usd_over_p_in": None,
        "failures_by_error_type": {},
        "program_error_failures": None,
        "other_failures": None,
        "per_use_records": False,
        "d_from_cost": True,
        "cost_usd": 0.0,
    }
    if "n" not in deploy:
        return empty

    success_rate = _num(deploy.get("success_rate"))
    by_type: dict[str, int] = {}
    uses_path = cell_dir / "deploy.json"
    uses_seen = False
    if uses_path.exists():
        uses = json.loads(uses_path.read_text()).get("uses") or []
        uses_seen = True
        for use in uses:
            if use.get("success"):
                continue
            by_type[str(use.get("error_type"))] = by_type.get(str(use.get("error_type")), 0) + 1
    program_errors = by_type.get("program_error") if uses_seen else None
    other = (sum(by_type.values()) - (program_errors or 0)) if uses_seen else None

    n = _num(deploy.get("n"))
    total_usd_over_p_in = meter.usd(deploy)
    meter.stages["deploy"] = {
        "usd_over_p_in": total_usd_over_p_in,
        "pw": None,
        "ratio_pw_over_usd": None,
        "note": "the stage records no prompt/cached/completion split, so the counts-based "
                "secondary unit is undefined here; the headline unit is the bill, as everywhere",
    }
    return {
        "deployed": True,
        "skipped": None,
        "pending_redeploy": pending,
        "n": deploy.get("n"),
        "successes": deploy.get("success_count"),
        "success_rate": success_rate,
        "q": (1.0 - success_rate) if success_rate is not None else None,
        # The old cells record no prompt/cached/completion split for the
        # deployment stage, so d in the price-weighted unit is taken from the
        # bill: one uncached extraction call, so cost/p_in equals the formula
        # up to the provider-routing factor.
        "d_pw": ratio(total_usd_over_p_in, n),
        "d_usd_over_p_in": ratio(total_usd_over_p_in, n),
        "d_raw_tokens_mean": _num(deploy.get("d_tokens_mean")),
        "tokens_total_raw": deploy.get("total_tokens"),
        "tokens_total_usd_over_p_in": total_usd_over_p_in,
        "failures_by_error_type": by_type,
        "program_error_failures": program_errors,
        "other_failures": other,
        "per_use_records": uses_seen,
        "d_from_cost": True,
        "cost_usd": cost_of(deploy),
    }


# ------------------------------------------------------------------- a cell


def build_cell_record(cell_dir: Path, model_cache_share: float | None = None) -> dict:
    build = json.loads((cell_dir / "build.json").read_text())
    model = build.get("model")
    meter = Meter(model)
    r = CACHE_RATIO.get(model, _num(build.get("cache_ratio_r")))

    exploration = read_exploration(cell_dir, build, meter, r, model_cache_share)
    translator = read_translator(cell_dir, build, meter)
    builder = read_builder(build, meter)
    verification = read_verification(build, meter)
    gate = read_gate(build, verification)
    deploy = read_deploy(cell_dir, build, meter, gate["admission_pending_redeploy"])
    doc_arm = read_doc_arm(cell_dir, build, meter, r)
    floor = floor_pw(model)

    missing_ca: list[str] = []
    if verification["resume_episodes_tokens_raw"]:
        missing_ca.append(
            "verification.resume_episodes, deterministic unit only (per-call token records are "
            "not on disk; the headline unit needs only the stage bill)"
        )
    if not exploration["cache_adjusted_deterministic_available"]:
        missing_ca.append(f"exploration trajectories {exploration['missing_trajectories']}")
    if not doc_arm["cache_adjusted_deterministic_available"]:
        missing_ca.append(f"doc_arm trajectories {doc_arm['missing_trajectories']}")

    # ---- compile price C.
    # Selected-arm reading: translator + selected initial + its refinements +
    # analyzer + resume episodes.  All-arms reading keeps every k arm instead.
    def compile_price(translator_tokens, initial_tokens, all_arms_tokens, refine, analyze, resume):
        without = add_opt(translator_tokens, initial_tokens)
        without_all = add_opt(translator_tokens, all_arms_tokens)
        repair = add_opt(refine, analyze, resume)
        return {
            "without": without,
            "without_all_arms": without_all,
            "with": add_opt(without, repair),
            "with_all_arms": add_opt(without_all, repair),
        }

    usd_price = compile_price(
        translator["tokens_usd_over_p_in"],
        builder["selected_initial_tokens_usd_over_p_in"],
        builder["all_arms_tokens_usd_over_p_in"],
        verification["builder_refinements_tokens_usd_over_p_in"],
        verification["analyzer_tokens_usd_over_p_in"],
        verification["resume_episodes_tokens_usd_over_p_in"],
    )
    pw_price = compile_price(
        translator["tokens_pw"],
        builder["selected_initial_tokens_pw"],
        builder["all_arms_tokens_pw"],
        verification["builder_refinements_tokens_pw"],
        verification["analyzer_tokens_pw"],
        verification["resume_episodes_tokens_pw"],
    )
    raw_price = compile_price(
        translator["tokens_raw"],
        builder["selected_initial_tokens_raw"],
        builder["all_arms_tokens_raw"],
        verification["builder_refinements_tokens_raw"],
        verification["analyzer_tokens_raw"],
        verification["resume_episodes_tokens_raw"],
    )
    # Deterministic secondary: every component but the resume episodes is a
    # single call, so only the "with repair" reading stays undefined.
    c_without_repair_ca = raw_price["without"]
    c_with_repair_ca_upper = add_opt(
        c_without_repair_ca,
        add_opt(
            verification["builder_refinements_tokens_raw"],
            verification["analyzer_tokens_raw"],
            verification["resume_episodes_tokens_raw"],
        ),
    )

    c_with_repair_cost = (
        translator["cost_usd"]
        + builder["selected_initial_cost_usd"]
        + verification["cost_usd_all"]
    )
    c_without_repair_cost = translator["cost_usd"] + builder["selected_initial_cost_usd"]

    # ---- headline constants, price-weighted unit, floor subtracted.
    q = deploy["q"]
    d = deploy["d_pw"]
    pi = exploration["pi"]
    p = gate["p_headline"]
    c_unsubtracted = exploration["c_attempt_pw"]
    c = sub_opt(c_unsubtracted, floor)
    l_doc_unsubtracted = doc_arm["L_doc_pw"]
    l_doc = sub_opt(l_doc_unsubtracted, floor)

    s_arrival = None
    if c is not None and q is not None and d is not None:
        s_arrival = (1.0 - q) * c - d
    s_doc = sub_opt(c, l_doc)
    build_numerator = add_opt(exploration["tokens_total_pw"], pw_price["with"])
    headline = {
        "unit": "price_weighted_tokens (pw)",
        "c": c,
        "c_unsubtracted": c_unsubtracted,
        "floor_pw": floor,
        "floor_raw_tokens": FLOOR_RAW_TOKENS.get(model or ""),
        "pi": pi,
        "p": p,
        # p is the GATE MARGIN of the final program (passed / GATE_K), not the
        # probability that a compile attempt is admitted.  One attempt is
        # observed per cell, so that probability is the indicator below.
        "gate_margin": p,
        "p_attempt": (
            None if gate["admitted_headline"] is None
            else (1.0 if gate["admitted_headline"] else 0.0)
        ),
        "admitted": gate["admitted_headline"],
        "admission_pending_redeploy": gate["admission_pending_redeploy"],
        "q": q,
        "d": d,
        "L_doc": l_doc,
        "L_doc_unsubtracted": l_doc_unsubtracted,
        "C_with_repair": pw_price["with"],
        "C_without_repair": pw_price["without"],
        "C_fail": None,  # filled in by apply_effective_price(), a per-model median
        "C_eff": None,
        "C_eff_gate_margin": None,
        "C_eff_c_over_p": c_eff(pw_price["with"], p),
        "s_arrival": s_arrival,
        "s_doc": s_doc,
        "share_prog": ratio(s_arrival, c),
        "share_doc": ratio(s_doc, c),
        "nstar_marginal": None,
        "nstar_build": None,
        "nstar_marginal_gate_margin": None,
        "nstar_build_gate_margin": None,
        "nstar_marginal_c_over_p": nstar(c_eff(pw_price["with"], p), s_arrival),
        "nstar_build_c_over_p": nstar(build_numerator, s_arrival),
        "exploration_total": exploration["tokens_total_pw"],
    }

    # ---- per-success appendix (M1 secondary reading).
    reactive_per_success = ratio(c, pi)
    program_per_success = None
    if c is not None and q is not None and d is not None and pi is not None:
        denominator = (1.0 - q) + q * pi
        program_per_success = ratio(d + q * c, denominator)
    s_success = sub_opt(reactive_per_success, program_per_success)
    per_success = {
        "c_attempt": c,
        "c_success_pw": exploration["c_success_pw"],
        "c_per_delivery_pw": exploration["c_per_delivery_pw"],
        "reactive_per_delivered_success": reactive_per_success,
        "program_per_delivered_success": program_per_success,
        "s_success": s_success,
        "share_success": ratio(s_success, reactive_per_success),
        "nstar_success": None,
        "nstar_success_gate_margin": None,
        "nstar_success_c_over_p": nstar(c_eff(pw_price["with"], p), s_success),
    }

    # ---- secondary units kept whole: raw and deterministic cache-adjusted.
    secondary: dict[str, Any] = {}
    for unit, values in {
        "usd_over_p_in": {
            "c_attempt": exploration["c_attempt_usd_over_p_in"],
            "c_success": exploration["c_success_usd_over_p_in"],
            "c_per_delivery": exploration["c_per_delivery_usd_over_p_in"],
            "L_doc": doc_arm["L_doc_usd_over_p_in"],
            "C_with_repair": usd_price["with"],
            "C_without_repair": usd_price["without"],
            "exploration_total": exploration["tokens_total_usd_over_p_in"],
            "d": deploy["d_usd_over_p_in"],
        },
        "raw": {
            "c_attempt": exploration["c_attempt_raw"],
            "c_success": exploration["c_success_raw"],
            "c_per_delivery": exploration["c_per_delivery_raw"],
            "L_doc": doc_arm["L_doc_raw"],
            "C_with_repair": raw_price["with"],
            "C_without_repair": raw_price["without"],
            "exploration_total": exploration["tokens_total_raw"],
            "d": deploy["d_raw_tokens_mean"],
        },
        "cache_adjusted_deterministic": {
            "c_attempt": exploration["c_attempt_cache_adjusted_deterministic"],
            "c_success": exploration["c_success_cache_adjusted_deterministic"],
            "c_per_delivery": exploration["c_per_delivery_cache_adjusted_deterministic"],
            "L_doc": doc_arm["L_doc_cache_adjusted_deterministic"],
            "C_with_repair": None,
            "C_without_repair": c_without_repair_ca,
            "exploration_total": exploration["tokens_total_cache_adjusted_deterministic"],
            "d": deploy["d_raw_tokens_mean"],
        },
        "cache_adjusted_deterministic_upper": {
            "c_attempt": exploration["c_attempt_cache_adjusted_deterministic"],
            "c_success": exploration["c_success_cache_adjusted_deterministic"],
            "c_per_delivery": exploration["c_per_delivery_cache_adjusted_deterministic"],
            "L_doc": doc_arm["L_doc_cache_adjusted_deterministic"],
            "C_with_repair": c_with_repair_ca_upper,
            "C_without_repair": c_without_repair_ca,
            "exploration_total": exploration["tokens_total_cache_adjusted_deterministic"],
            "d": deploy["d_raw_tokens_mean"],
        },
    }.items():
        entry: dict[str, Any] = {}
        for c_name in ("c_attempt", "c_success", "c_per_delivery"):
            c_value = values[c_name]
            s_prog_u = None
            if c_value is not None and q is not None and values["d"] is not None:
                s_prog_u = (1.0 - q) * c_value - values["d"]
            s_doc_u = sub_opt(c_value, values["L_doc"])
            entry[c_name] = {
                "c": c_value,
                "s_doc": s_doc_u,
                "share_doc": ratio(s_doc_u, c_value),
                "s_prog": s_prog_u,
                "share_prog": ratio(s_prog_u, c_value),
                "s_prog_for_nstar": s_prog_u,
                "nstar_build": None,
                "nstar_marginal_headline_p": None,
                "nstar_build_c_over_p": nstar(
                    add_opt(values["exploration_total"], values["C_with_repair"]), s_prog_u
                ),
                "nstar_marginal_headline_p_c_over_p": nstar(
                    c_eff(values["C_with_repair"], p), s_prog_u
                ),
                "nstar_marginal_5of5": nstar(
                    c_eff(values["C_with_repair"], 1.0 if gate["admitted_5of5"] else 0.0), s_prog_u
                ),
                "nstar_marginal_protocol": nstar(
                    c_eff(values["C_with_repair"], 1.0 if gate["admitted_protocol"] else 0.0),
                    s_prog_u,
                ),
            }
        entry["d"] = values["d"]
        entry["C_with_repair"] = values["C_with_repair"]
        entry["C_without_repair"] = values["C_without_repair"]
        entry["L_doc"] = values["L_doc"]
        entry["exploration_total"] = values["exploration_total"]
        secondary[unit] = entry

    # ---- dollars, summed from the stage records rather than total_cost_usd.
    cost_components = {
        "exploration": exploration["cost_usd"],
        "translator": translator["cost_usd"],
        "builder_all_arms": builder["all_arms_cost_usd"],
        "verification_analyzer": verification["analyzer_cost_usd"],
        "verification_resume_episodes": verification["resume_episodes_cost_usd"],
        "verification_builder_refinements": verification["builder_refinements_cost_usd"],
        "deploy": deploy["cost_usd"],
        "doc_arm": doc_arm["cost_usd"],
    }
    total_cost_usd = round(sum(cost_components.values()), 8)
    recorded_total = _num(build.get("total_cost_usd"))

    counts_diagnostic = meter.diagnostic()

    notes: list[str] = []
    if recorded_total is None:
        notes.append(
            "build.json carries no total_cost_usd (budget stop); the sum here is from the stage records"
        )
    elif abs(recorded_total - total_cost_usd) > 1e-4:
        notes.append(
            f"summed stage cost {total_cost_usd:.6f} differs from recorded total_cost_usd "
            f"{recorded_total:.6f}"
        )
    if gate["admission_pending_redeploy"]:
        notes.append(
            f"initial gate at k={PROTOCOL_K} passed {gate[f'gate_k{PROTOCOL_K}_passed']}/"
            f"{gate[f'gate_k{PROTOCOL_K}_total']} yet verification declared the type "
            "unautomatable: the repair loop discarded an admissible program, p is read off that "
            "gate and q, d wait for the redeploy run"
        )
    elif gate["gate_k3_passed"] == gate["gate_k3_total"] and verification["unautomatable"]:
        notes.append("gate_per_k k=3 passed 5/5 yet verification declared unautomatable")
    if (
        gate["p_after_repair"] is not None
        and gate["gate_k3_passed"] == gate["gate_k3_total"]
        and gate["gate_after_repair_passed"] != gate["gate_k3_passed"]
        and verification["refinements"] == 0
    ):
        notes.append(
            "gate_after_repair disagrees with gate_per_k k=3 although no refinement happened "
            "(the same program was re-gated with a different outcome)"
        )
    if gate["redeploy_present"]:
        notes.append(
            f"admission read off the redeploy gate ({gate['redeploy_gate_passed']}/"
            f"{gate['redeploy_gate_total']}, artifact {gate['redeploy_source_artifact']}); "
            "verification.unautomatable stays true and gate_after_repair stays null by design"
        )
    if verification["unautomatable"] and deploy["deployed"] and not gate["redeploy_present"]:
        notes.append("deployed although verification declared unautomatable")
    if not verification["unautomatable"] and not deploy["deployed"]:
        notes.append("not deployed although verification did not declare unautomatable")
    calls = (
        (build.get("builder") or {})
        .get("selected_arm", {})
        .get("initial_plus_refinements", {})
        .get("calls")
    )
    if calls is not None and verification["refinements"] is not None:
        # Every cell records calls == refinements, i.e. the field counts the
        # refinement calls only although its token totals include the initial
        # call as well.  That systematic off-by-one is stated once, in the
        # table's `conventions`, so only a different disagreement is flagged.
        if calls not in (verification["refinements"], verification["refinements"] + 1):
            notes.append(
                f"builder.selected_arm.initial_plus_refinements.calls = {calls} but the record has "
                f"1 initial call plus {verification['refinements']} refinements"
            )
    for mismatch in exploration["trajectory_total_mismatches"]:
        notes.append(f"exploration trajectory disagrees with build.json totals: {mismatch}")
    if exploration["edge_cases"]["prompt_shrank_calls"]:
        notes.append(
            f"{exploration['edge_cases']['prompt_shrank_calls']} exploration call(s) sent a shorter "
            "prompt than the previous call (charged fresh in full by the deterministic unit)"
        )
    gap = exploration["totals_vs_parts_usd_over_p_in_gap"]
    if gap is not None and abs(gap) > 0.01:
        notes.append(
            f"exploration.totals disagrees with the sum of its episodes and reflections by "
            f"{gap * 100:.1f}% in the billed unit"
        )
    reused_present = any(e["reused"] for e in exploration["episodes"])
    if reused_present and not exploration["reused_episodes_cache_imputed"]:
        notes.append(
            "exploration reused t12_grid episodes but nothing was imputed: the cell's fresh "
            f"attempts report a cache share of {exploration['reused_cache_share_used']}, so the "
            "reused prompts stay charged fresh in full (c is an upper bound here)"
        )
    if meter.missing_cached_calls:
        notes.append(
            f"{meter.missing_cached_calls} call(s) carry no provider cached_tokens; read as 0"
        )

    return {
        "model": model,
        "model_slug": cell_dir.parent.name,
        "family": cell_dir.name,
        "dir": str(cell_dir),
        "prices": {
            **(meter.prices or {}),
            "r_c": meter.r_c,
            "r_o": meter.r_o,
        },
        "cache_ratio_r_deterministic": r,
        "stages_done": build.get("stages_done"),
        "wall_s": build.get("wall_s"),
        "headline": headline,
        "per_success_appendix": per_success,
        "exploration": exploration,
        "translator": translator,
        "builder": builder,
        "verification": verification,
        "gate": gate,
        "deploy": deploy,
        "doc_arm": doc_arm,
        "compile_price": {
            "C_with_repair_usd_over_p_in": usd_price["with"],
            "C_without_repair_usd_over_p_in": usd_price["without"],
            "C_with_repair_all_arms_usd_over_p_in": usd_price["with_all_arms"],
            "C_without_repair_all_arms_usd_over_p_in": usd_price["without_all_arms"],
            "C_with_repair_pw": pw_price["with"],
            "C_without_repair_pw": pw_price["without"],
            "C_with_repair_raw": raw_price["with"],
            "C_without_repair_raw": raw_price["without"],
            "C_with_repair_all_arms_raw": raw_price["with_all_arms"],
            "C_without_repair_all_arms_raw": raw_price["without_all_arms"],
            "C_without_repair_cache_adjusted_deterministic": c_without_repair_ca,
            "C_with_repair_cache_adjusted_deterministic": None,
            "C_with_repair_cache_adjusted_deterministic_upper": c_with_repair_ca_upper,
            "C_with_repair_cost_usd": c_with_repair_cost,
            "C_without_repair_cost_usd": c_without_repair_cost,
        },
        "secondary_units": secondary,
        "counts_diagnostic": counts_diagnostic,
        "cached_tokens_missing_calls": meter.missing_cached_calls,
        "cost_usd": {
            "components": cost_components,
            "total_summed": total_cost_usd,
            "total_recorded_field": recorded_total,
        },
        "missing_cache_adjusted_deterministic": missing_ca,
        "notes": notes,
    }


# -------------------------------------------------------------- aggregates


def _defined(values: Iterable[Any]) -> list[float]:
    out = []
    for value in values:
        number = _num(value)
        if number is not None:
            out.append(number)
    return out


def _path(record: dict, path: str) -> Any:
    node: Any = record
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


AGGREGATED = [
    "headline.c",
    "headline.c_unsubtracted",
    "headline.pi",
    "headline.p",
    "headline.q",
    "headline.d",
    "headline.L_doc",
    "headline.C_with_repair",
    "headline.C_without_repair",
    "headline.s_arrival",
    "headline.s_doc",
    "headline.share_prog",
    "headline.share_doc",
    "headline.nstar_marginal",
    "headline.nstar_build",
    "per_success_appendix.reactive_per_delivered_success",
    "per_success_appendix.program_per_delivered_success",
    "per_success_appendix.s_success",
    "per_success_appendix.nstar_success",
    "verification.refinements",
    "gate.p_initial_k1",
    "gate.p_initial_k2",
    "gate.p_initial_k3",
    "gate.p_after_repair",
    "counts_diagnostic.ratio_pw_over_usd",
    "secondary_units.usd_over_p_in.c_attempt.c",
    "secondary_units.raw.c_attempt.c",
    "secondary_units.raw.C_with_repair",
    "secondary_units.cache_adjusted_deterministic.c_attempt.c",
    "cost_usd.total_summed",
]


def aggregate(records: list[dict]) -> dict:
    out: dict[str, Any] = {
        "cells": len(records),
        "cells_deployed": sum(1 for r in records if r["deploy"]["deployed"]),
        "cells_admitted": sum(1 for r in records if r["gate"]["admitted_headline"]),
        "cells_admission_pending_redeploy": sum(
            1 for r in records if r["gate"]["admission_pending_redeploy"]
        ),
        "cells_admitted_via_redeploy": sum(
            1 for r in records if r["gate"]["redeploy_present"] and r["gate"]["admitted_headline"]
        ),
        "cells_redeployed": sum(1 for r in records if r["gate"]["redeploy_present"]),
        "cells_admitted_5of5": sum(1 for r in records if r["gate"]["admitted_5of5"]),
        "cells_admitted_protocol": sum(1 for r in records if r["gate"]["admitted_protocol"]),
        "cells_unautomatable": sum(1 for r in records if r["verification"]["unautomatable"]),
        "cells_counts_diagnostic_outside_band": sum(
            1 for r in records if r["counts_diagnostic"]["outside_band"]
        ),
        "total_cost_usd_summed": round(sum(r["cost_usd"]["total_summed"] for r in records), 6),
        "cached_tokens_missing_calls": sum(r["cached_tokens_missing_calls"] for r in records),
        "quantities": {},
    }
    for path in AGGREGATED:
        values = _defined(_path(r, path) for r in records)
        infinite = sum(1 for r in records if _path(r, path) == INF)
        out["quantities"][path] = {
            "n_defined": len(values),
            "n_infinite": infinite,
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }
    return out


# --------------------------------------------------------------- rendering


def fmt(value: Any, digits: int = 2, scale: float = 1.0) -> str:
    if value == INF:
        return "inf"
    number = _num(value)
    if number is None:
        return "—"
    number = number / scale
    if digits == 0:
        return f"{number:,.0f}"
    return f"{number:.{digits}f}"


def fmt_k(value: Any) -> str:
    """Tokens in thousands."""
    if value == INF:
        return "inf"
    number = _num(value)
    if number is None:
        return "—"
    return f"{number / 1000:,.1f}k"


def pending(record: dict) -> bool:
    return bool(record["gate"]["admission_pending_redeploy"])


def cell_or_pending(record: dict, value: Any, render) -> str:
    """A pending-redeploy cell shows "pending" where the redeploy would fill in."""
    if pending(record) and _num(value) is None and value != INF:
        return "pending"
    return render(value)


def fmt_admitted(record: dict) -> str:
    if pending(record):
        return "pending"
    admitted = record["gate"]["admitted_headline"]
    if admitted is None:
        return "—"
    return "yes" if admitted else "no"


MD_COLUMNS = [
    ("family", lambda r: r["family"]),
    ("pi", lambda r: fmt(r["headline"]["pi"])),
    ("c", lambda r: fmt_k(r["headline"]["c"])),
    ("C_with_repair", lambda r: fmt_k(r["headline"]["C_with_repair"])),
    ("refine", lambda r: fmt(r["verification"]["refinements"], 0)),
    ("p_k1", lambda r: fmt(r["gate"]["p_initial_k1"])),
    ("p_k2", lambda r: fmt(r["gate"]["p_initial_k2"])),
    ("p_k3", lambda r: fmt(r["gate"]["p_initial_k3"])),
    ("p_after", lambda r: fmt(r["gate"]["p_after_repair"])),
    ("admitted", fmt_admitted),
    ("q", lambda r: cell_or_pending(r, r["headline"]["q"], lambda v: fmt(v, 3))),
    ("d", lambda r: cell_or_pending(r, r["headline"]["d"], lambda v: fmt(v, 0))),
    ("L_doc", lambda r: fmt_k(r["headline"]["L_doc"])),
    ("share_doc", lambda r: fmt(r["headline"]["share_doc"], 3)),
    ("share_prog", lambda r: cell_or_pending(r, r["headline"]["share_prog"], lambda v: fmt(v, 3))),
    (
        "N*_marg",
        lambda r: cell_or_pending(r, r["headline"]["nstar_marginal"], lambda v: fmt(v, 3)),
    ),
    ("N*_build", lambda r: cell_or_pending(r, r["headline"]["nstar_build"], lambda v: fmt(v, 3))),
    ("cost_usd", lambda r: fmt(r["cost_usd"]["total_summed"], 4)),
]


APPENDIX_COLUMNS = [
    ("family", lambda r: r["family"]),
    ("pi", lambda r: fmt(r["headline"]["pi"])),
    ("c_attempt", lambda r: fmt_k(r["per_success_appendix"]["c_attempt"])),
    ("c_success", lambda r: fmt_k(r["per_success_appendix"]["c_success_pw"])),
    (
        "reactive/success",
        lambda r: fmt_k(r["per_success_appendix"]["reactive_per_delivered_success"]),
    ),
    (
        "program/success",
        lambda r: cell_or_pending(
            r, r["per_success_appendix"]["program_per_delivered_success"], fmt_k
        ),
    ),
    ("s_success", lambda r: cell_or_pending(r, r["per_success_appendix"]["s_success"], fmt_k)),
    (
        "share_success",
        lambda r: cell_or_pending(
            r, r["per_success_appendix"]["share_success"], lambda v: fmt(v, 3)
        ),
    ),
    (
        "N*_success",
        lambda r: cell_or_pending(
            r, r["per_success_appendix"]["nstar_success"], lambda v: fmt(v, 3)
        ),
    ),
]


SUMMARY_ROWS = [
    "headline.pi",
    "headline.c",
    "headline.C_with_repair",
    "verification.refinements",
    "gate.p_initial_k3",
    "gate.p_after_repair",
    "headline.p",
    "headline.q",
    "headline.d",
    "headline.L_doc",
    "headline.share_doc",
    "headline.share_prog",
    "headline.nstar_marginal",
    "headline.nstar_build",
    "cost_usd.total_summed",
]

BIG_ROWS = {
    "headline.c",
    "headline.C_with_repair",
    "headline.L_doc",
    "headline.d",
}


def render_markdown(records: list[dict], per_model: dict) -> str:
    lines = ["# t16_build constants table", ""]
    lines.append(
        "Unit: price-weighted tokens (`pw`), `(prompt - cached) + r_c * cached + r_o * "
        "completion`, with r_c and r_o from the OpenRouter list prices fetched "
        f"{PRICE_SHEET_FETCHED} from {PRICE_SHEET_SOURCE}.  `c` is the mean price-weighted cost "
        "of one exploration ATTEMPT (every attempt, "
        "reflection retries and their reflection calls included), minus the per-model global "
        "floor; `L_doc` is floor-subtracted too.  `gate` is the final program's margin, its "
        f"bindings passed over {GATE_K} on the post-repair gate (or, once a cell has been "
        f"redeployed, the redeploy gate); a program is admitted at {GATE_MIN_PASS} of {GATE_K}.  "
        "N*_marg = C_eff / s_arrival and N*_build = (exploration total + C_eff) / s_arrival, "
        "with C_eff = C_with_repair for an admitted cell and inf otherwise: one compile attempt "
        "is observed per cell, so `gate` is the admitted program's margin and not the probability "
        "that an attempt is admitted, and the reading that charged (1/gate - 1) failed attempts "
        "is kept in the JSON as *_gate_margin.  The expected price for the NEXT family is the "
        "population reading under each table.  s_arrival = (1 - q) c - d.  `—` means "
        "undefined, `inf` means the saving was not positive or the cell was not admitted, "
        "`pending` marks a cell "
        "whose admissible program the repair loop discarded and whose redeploy run is scheduled.  "
        "d comes from the deployment bill, the one stage of these cells with no "
        "prompt/cached/completion split.  The bill-based unit `usd_over_p_in` with its per-stage "
        "pw/USD diagnostic, the raw unit, the deterministic cache-adjusted unit and the 5-of-5 "
        "and protocol admission readings are in constants_table.json."
    )
    lines.append("")
    for model_slug in sorted({r["model_slug"] for r in records}):
        cells = [r for r in records if r["model_slug"] == model_slug]
        model = cells[0]["model"]
        prices = cells[0]["prices"]
        lines.append(
            f"## {model}  (r_c = {prices.get('r_c'):.4f}, r_o = {prices.get('r_o'):.2f}, "
            f"floor = {FLOOR_RAW_TOKENS.get(model)} raw tokens)"
        )
        lines.append("")
        lines.append("| " + " | ".join(name for name, _ in MD_COLUMNS) + " |")
        lines.append("|" + "|".join("---" for _ in MD_COLUMNS) + "|")
        for record in sorted(cells, key=lambda r: r["family"]):
            lines.append("| " + " | ".join(render(record) for _, render in MD_COLUMNS) + " |")
        lines.append("")
        lines.append("Per-success appendix (M1 secondary reading).")
        lines.append("")
        lines.append("| " + " | ".join(name for name, _ in APPENDIX_COLUMNS) + " |")
        lines.append("|" + "|".join("---" for _ in APPENDIX_COLUMNS) + "|")
        for record in sorted(cells, key=lambda r: r["family"]):
            lines.append(
                "| " + " | ".join(render(record) for _, render in APPENDIX_COLUMNS) + " |"
            )
        summary = per_model[model_slug]
        lines.append("")
        lines.append(
            f"{summary['cells']} cells, {summary['cells_deployed']} deployed, "
            f"{summary['cells_admitted']} admitted at {GATE_MIN_PASS}/{GATE_K}, "
            f"{summary['cells_admission_pending_redeploy']} pending redeploy, "
            f"{summary['cells_redeployed']} redeployed, "
            f"{summary['cells_unautomatable']} declared unautomatable; "
            f"total ${summary['total_cost_usd_summed']:.4f}."
        )
        quantities = summary["quantities"]
        lines.append("")
        lines.append("| quantity | n | mean | median |")
        lines.append("|---|---|---|---|")
        for path in SUMMARY_ROWS:
            entry = quantities[path]
            render_value = fmt_k if path in BIG_ROWS else (lambda v: fmt(v, 4))
            lines.append(
                f"| {path} | {entry['n_defined']} | {render_value(entry['mean'])} | "
                f"{render_value(entry['median'])} |"
            )
        lines.append("")
    notes = [(r["model_slug"], r["family"], note) for r in records for note in r["notes"]]
    if notes:
        lines.append("## Flagged records")
        lines.append("")
        for slug, family, note in notes:
            lines.append(f"- {slug} / {family}: {note}")
        lines.append("")
    missing = sorted({m for r in records for m in r["missing_cache_adjusted_deterministic"]})
    if missing:
        lines.append("## Not computable (deterministic secondary unit only)")
        lines.append("")
        for item in missing:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI


def find_cells(root: Path) -> list[Path]:
    cells = []
    for model_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if ".spoiled-" in model_dir.name:
            continue
        for cell_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
            if ".spoiled-" in cell_dir.name:
                continue
            if (cell_dir / "build.json").exists():
                cells.append(cell_dir)
    return cells


def apply_effective_price(records: list[dict]) -> dict[str, dict[str, Any]]:
    """Fill in C_eff and the N* readings, per cell and per model.

    Per cell, ONE compile attempt is observed, so the probability that an
    attempt is admitted is the indicator ``p_attempt``: C_eff = C_with_repair
    for an admitted cell and "inf" otherwise.  The gate margin (passed /
    GATE_K) is the admitted program's margin, not that probability, so the
    reading that charged (1/gate_margin - 1) failed attempts to a cell that was
    admitted on its first attempt is kept only as ``*_gate_margin``.

    Per model, the population reading prices a family DRAWN FROM THIS
    POPULATION: with admission_rate = admitted / cells, C_eff_population =
    C_admitted_median + (1/admission_rate - 1) * C_fail, and nstar_population =
    C_eff_population / s_median over the admitted cells.
    """
    populations: dict[str, dict[str, Any]] = {}
    for model in sorted({str(r["model"]) for r in records}):
        cells = [r for r in records if str(r["model"]) == model]
        admitted = [c for c in cells if c["gate"]["admitted_headline"] is True]
        failed = [c for c in cells if c["gate"]["admitted_headline"] is False]
        units = sorted({u for c in cells for u in c["secondary_units"]})

        def median_over(group: list[dict], unit: str) -> float | None:
            values = _defined(
                (c["headline"] if unit == "headline" else c["secondary_units"][unit])[
                    "C_with_repair"
                ]
                for c in group
            )
            return statistics.median(values) if values else None

        fails = {unit: median_over(failed, unit) for unit in ("headline", *units)}

        for record in cells:
            head = record["headline"]
            p_attempt = head["p_attempt"]
            margin = head["gate_margin"]
            head["C_fail"] = fails["headline"]
            head["C_eff"] = effective_price(head["C_with_repair"], p_attempt, fails["headline"])
            head["C_eff_gate_margin"] = effective_price(
                head["C_with_repair"], margin, fails["headline"]
            )
            head["nstar_marginal"] = nstar(head["C_eff"], head["s_arrival"])
            head["nstar_build"] = nstar(
                INF if head["C_eff"] == INF
                else add_opt(head["exploration_total"], head["C_eff"]),
                head["s_arrival"],
            )
            head["nstar_marginal_gate_margin"] = nstar(
                head["C_eff_gate_margin"], head["s_arrival"]
            )
            head["nstar_build_gate_margin"] = nstar(
                INF if head["C_eff_gate_margin"] == INF
                else add_opt(head["exploration_total"], head["C_eff_gate_margin"]),
                head["s_arrival"],
            )
            appendix = record["per_success_appendix"]
            appendix["nstar_success"] = nstar(head["C_eff"], appendix["s_success"])
            appendix["nstar_success_gate_margin"] = nstar(
                head["C_eff_gate_margin"], appendix["s_success"]
            )
            for unit, entry in record["secondary_units"].items():
                unit_eff = effective_price(entry["C_with_repair"], p_attempt, fails.get(unit))
                entry["C_fail"] = fails.get(unit)
                entry["C_eff"] = unit_eff
                entry["C_eff_gate_margin"] = effective_price(
                    entry["C_with_repair"], margin, fails.get(unit)
                )
                for c_name in ("c_attempt", "c_success", "c_per_delivery"):
                    reading = entry[c_name]
                    saving = reading["s_prog_for_nstar"]
                    reading["nstar_marginal_headline_p"] = nstar(unit_eff, saving)
                    reading["nstar_build"] = nstar(
                        INF if unit_eff == INF
                        else add_opt(entry["exploration_total"], unit_eff),
                        saving,
                    )
                    reading["nstar_marginal_gate_margin"] = nstar(
                        entry["C_eff_gate_margin"], saving
                    )

        admission_rate = ratio(len(admitted), len(cells))
        c_admitted = median_over(admitted, "headline")
        c_eff_population = effective_price(c_admitted, admission_rate, fails["headline"])
        savings = _defined(c["headline"]["s_arrival"] for c in admitted)
        s_median = statistics.median(savings) if savings else None
        populations[model] = {
            "cells": len(cells),
            "cells_admitted": len(admitted),
            "cells_not_admitted": len(failed),
            "admission_rate": admission_rate,
            "C_admitted_median": c_admitted,
            "C_fail": fails["headline"],
            "C_eff_population": c_eff_population,
            "s_median": s_median,
            "nstar_population": nstar(c_eff_population, s_median),
            "C_fail_per_unit": {unit: fails[unit] for unit in units},
        }
    return populations


def model_cache_shares(cells: list[Path]) -> dict[str, float]:
    """Mean cache share of the fresh exploration and doc-arm episodes, per model.

    The fallback for a cell that has neither a fresh exploration attempt nor a
    doc arm of its own to measure.
    """
    sums: dict[str, list[float]] = {}
    for cell in cells:
        build = json.loads((cell / "build.json").read_text())
        evidence = fresh_cache_sums(build)
        entry = sums.setdefault(str(build.get("model")), [0.0, 0.0])
        entry[0] += evidence["cached"]
        entry[1] += evidence["prompt"]
    return {
        model: (cached / prompt) for model, (cached, prompt) in sums.items() if prompt
    }


def build_table(root: Path) -> dict:
    cells = find_cells(root)
    shares = model_cache_shares(cells)
    records = [
        build_cell_record(
            cell,
            shares.get(
                str(json.loads((cell / "build.json").read_text()).get("model"))
            ),
        )
        for cell in cells
    ]
    population = apply_effective_price(records)
    per_model = {
        slug: aggregate([r for r in records if r["model_slug"] == slug])
        for slug in sorted({r["model_slug"] for r in records})
    }
    return {
        "record_type": "constants_table",
        "root": str(root),
        "price_sheet": PRICE_SHEET,
        "price_sheet_fetched": PRICE_SHEET_FETCHED,
        "price_sheet_source": PRICE_SHEET_SOURCE,
        "floor_raw_tokens": FLOOR_RAW_TOKENS,
        "gate_min_pass": GATE_MIN_PASS,
        "gate_bindings": GATE_K,
        "cache_ratio_r_deterministic": CACHE_RATIO,
        "cells": records,
        "per_model": per_model,
        "population": population,
        "conventions": {
            "unit": (
                "HEADLINE: price-weighted tokens (pw), tokens_pw = (prompt_tokens - "
                "cached_tokens) + r_c * cached_tokens + r_o * completion_tokens, per stage and "
                "per cell, for c, C, L_doc and every derived quantity.  r_c = p_c / p_in and "
                f"r_o = p_o / p_in from price_sheet, fetched {PRICE_SHEET_FETCHED} from "
                f"{PRICE_SHEET_SOURCE}: GLM p_in 1.5e-7, p_c 3e-8, p_o 5e-7 (r_c 0.20, r_o 3.33) "
                "and DeepSeek p_in 2.2e-7, p_c 7e-9, p_o 6.6e-7 (r_c 0.0318, r_o 3.0).  This "
                "supersedes the sheet in docs/cache-adjusted-accounting.md section 1, which is "
                "stale: GLM's three prices were low by exactly 2x (ratios unchanged) and "
                "DeepSeek's p_in was low by 10x, which is where its 0.318 and 30 came from.  "
                "cached_tokens is the provider-reported field (OpenRouter "
                "usage.prompt_tokens_details.cached_tokens); a missing one is read as 0 and "
                "counted in cached_tokens_missing_calls.  The formula is linear, so it applies to "
                "stage totals directly and every stage with a split is computable, the "
                "verification resume episodes included.  A per-call regression showed the bill "
                "equals this formula call by call up to a discrete OpenRouter provider-routing "
                "factor (some GLM calls billed at 0.5x, some DeepSeek calls at 2x), which is "
                "noise for token efficiency."
            ),
            "usd_over_p_in": (
                "SECONDARY: recorded cost_usd / p_in, the bill in the same dimension.  Each stage "
                "carries ratio_pw_over_usd in counts_diagnostic.per_stage: 1 where the bill "
                "followed the sheet, otherwise the routing factor (and image surcharges).  "
                "Reported, never applied."
            ),
            "floor": (
                "A per-model global floor is subtracted from c and from L_doc: "
                f"{FLOOR_RAW_TOKENS} raw tokens, measured on 18 no-task episodes with dispersion "
                "under 0.5%.  It is charged as uncached prompt with no completion, whose "
                "coefficient in the price-weighted formula is 1, so the floor in the headline "
                "unit is the same number as the raw floor.  c_unsubtracted and L_doc_unsubtracted "
                "keep the unsubtracted readings."
            ),
            "reused_episode_cache_imputation": (
                "Exploration reused t12_grid episodes for some seeds (per_episode[i].reused). "
                "Those trajectory records carry prompt_tokens, completion_tokens and cost_usd "
                "only, with no cached_tokens, so the price-weighted formula would charge their "
                "whole prompt as fresh.  Each such episode gets cached_tokens = prompt_tokens * "
                "(cache share of the SAME cell's fresh exploration attempts AND its doc-arm "
                "episodes: same agent, same family, same prompt structure, so a single cold "
                "exploration episode reporting 0 does not decide the share alone).  A cell with "
                "neither falls back to the model's mean over all such episodes.  "
                "reused_cache_share_source says which: \"cell fresh+doc\", \"cell fresh\", "
                "\"cell doc\" or \"model mean\".  exploration.totals already sums those prompts with cached 0, so the "
                "imputed cache is added to the stage total's cached count once.  Per cell: "
                "reused_episodes_cache_imputed, reused_cache_share_used, "
                "reused_cache_share_source, reused_cache_tokens_imputed, and "
                "c_attempt_pw_no_imputation.  Their cost_usd is real, so "
                "secondary_units.usd_over_p_in is an independent bound that no imputation touches."
            ),
            "c_headline": (
                "Mean price-weighted tokens per exploration ATTEMPT.  explore.py appends the first "
                "episode of an instance and each of its up-to-two reflection retries to "
                "per_episode, and its summarise() builds exploration.totals from the episode "
                "usages AND the reflection-call usages.  The headline therefore divides "
                "exploration.totals by the number of attempt episodes: a reflection call is part "
                "of the cost of the reactive attempt it enables.  "
                "exploration.c_attempt_pw_episodes_only drops the reflection calls."
            ),
            "admission": (
                f"HEADLINE p = post-repair gate passed / {GATE_K}, admitted at "
                f"{GATE_MIN_PASS}/{GATE_K} (GATE_MIN_PASS).  A cell whose verification "
                "declared the type unautomatable has p = 0, unless the initial gate at "
                f"k = {PROTOCOL_K} (the k the protocol deploys) passed at least "
                f"{GATE_MIN_PASS}/{GATE_K}; that cell is marked "
                "admission_pending_redeploy, carries p from that gate, and leaves q and d empty "
                "until the scheduled redeploy run.  Once the redeploy has run, build.json carries "
                "a `redeploy` section: its gate is then the admission evidence (admission_source "
                "= \"redeploy\"), q and d come from the deploy section that run wrote, and "
                "verification.unautomatable stays true and gate_after_repair stays null by "
                "design.  admitted_5of5 and admitted_protocol keep the older readings, so "
                "admitted_protocol (not unautomatable) reads False on a redeployed cell."
            ),
            "C_with_repair": (
                "translator + selected builder arm's initial call + its refinements + "
                "verification analyzer + verification resume episodes.  Finished cells UNDER-COUNT "
                "it: builder replies rejected before they became an arm were not recorded at the "
                "time, so their tokens are missing from every reading here."
            ),
            "C_without_repair": "translator + selected builder arm's initial call",
            "nstar_build": "(exploration total + C_eff) / s_arrival",
            "nstar_marginal": "C_eff / s_arrival",
            "C_eff": (
                "PER CELL: one compile attempt is observed, so p_attempt is an indicator and "
                "C_eff = C_with_repair for an admitted cell, inf otherwise.  N*_marginal = C_eff "
                "/ s_arrival and N*_build = (exploration total + C_eff) / s_arrival.  The gate "
                "margin (gate_margin = bindings passed / GATE_K, the old p_after_repair) is the "
                "ADMITTED PROGRAM'S MARGIN, not the probability that an attempt is admitted: "
                "reading it as that probability charges a phantom failed attempt to a cell that "
                "passed on its first attempt, so C_eff_gate_margin, nstar_marginal_gate_margin, "
                "nstar_build_gate_margin and nstar_success_gate_margin keep that reading as a "
                "secondary one, next to the older C / gate_margin in *_c_over_p."
            ),
            "population": (
                "PER MODEL, in the headline unit: admission_rate = admitted cells / cells, "
                "C_admitted_median and C_fail are the medians of C_with_repair over the admitted "
                "and the not-admitted cells, C_eff_population = C_admitted_median + "
                "(1/admission_rate - 1) * C_fail, s_median is the median s_arrival over the "
                "admitted cells, and nstar_population = C_eff_population / s_median.  This is the "
                "EXPECTED PRICE OF AN ADMITTED PROGRAM for a family drawn from this population "
                "(the rejected attempts a new family should expect, charged at the price its "
                "failures actually cost), which is the quantity the policy's prior starts from.  "
                "A per-cell N* prices the family that was actually compiled; this one prices the "
                "next one."
            ),
            "s_arrival": "(1 - q) * c - d, the per-arrival saving; s_doc = c - L_doc",
            "per_success_appendix": (
                "Reactive cost per delivered success = c / pi; program cost per delivered success "
                "= (d + q * c) / ((1 - q) + q * pi); s_success is their difference and N*_success "
                "= C_eff / s_success.  Secondary to the per-arrival headline."
            ),
            "d": (
                "d = (deployment cost_usd / p_in) / n.  The deployment stage of these cells is "
                "the one stage with no prompt/cached/completion split (calls_detail was added to "
                "deploy_runner.py after these runs), so the price-weighted formula cannot be "
                "applied to it directly.  A deployment use is one uncached extraction call, for "
                "which cost / p_in = prompt + r_o * completion is that formula, up to the "
                "provider-routing factor.  secondary_units.raw.d keeps the recorded "
                "d_tokens_mean."
            ),
            "counts_diagnostic": (
                "counts_diagnostic.ratio_pw_over_usd = pw / (cost_usd / p_in), per stage and per "
                f"cell over the stages where both exist.  {list(CROSS_CHECK_BAND)} is the band "
                "outside which outside_band is set.  Reported, never applied."
            ),
            "cache_adjusted_deterministic": (
                "SECONDARY unit kept from the earlier table: "
                "docs/cache-adjusted-accounting.md section 8, within an episode "
                "effective_i = (prompt_i - prompt_{i-1}) + r * prompt_{i-1} + completion_i, first "
                "call fresh in full, episodes cold to each other, single calls fresh in full.  It "
                "needs per-call records, so the verification resume episodes stay undefined and "
                "the *_upper variant charges them at raw."
            ),
            "builder_calls_field": (
                "builder.selected_arm.initial_plus_refinements.calls counts the refinement calls "
                "only in every cell on disk, although the token totals beside it include the "
                "initial builder call; the field is not used here"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args(argv)

    root = args.root
    out_json = args.out_json or root / "constants_table.json"
    out_md = args.out_md or root / "constants_table.md"

    table = build_table(root)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(table, indent=1, allow_nan=False) + "\n")
    out_md.write_text(render_markdown(table["cells"], table["per_model"]))
    print(f"{len(table['cells'])} cells -> {out_json}")
    print(f"{len(table['cells'])} cells -> {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

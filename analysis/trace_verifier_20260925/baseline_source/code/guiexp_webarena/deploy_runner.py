"""Deployment stage: 30 uses of the verified program (d and q).

The WebArena mirror of guiexp_android/deploy_runner.py, same contract:

  * one use = one goal filled from a FRESH binding (deploy seeds, disjoint
    from building/gate/doc seeds);
  * per use: ONE extraction model call (goal -> binding JSON), one bounded
    retry when the type check rejects; extraction failures count into q;
  * the extracted binding drives the program; the DB checker judges;
  * d = mean tokens per use (extraction chain only), q = failure rate.
"""

from __future__ import annotations

import json
import time

from .compiler import _openai_client
from .family import (
    FAMILY,
    BINDING_FIELDS,
    binding_to_params,
    params_to_binding,
)
from .program_runtime import ProgramRunner

EXTRACTION_SYSTEM = "You extract structured task fields from a request. You reply with a JSON object only."

_FIELD_HINTS = {
    "CommentPost": (
        "forum: the forum's short name as it appears in the request or in\n"
        "the site's /f/<name> URLs (one word, letters/digits/underscore).\n"
        "title: the post's full title inside the quotes after 'titled'.\n"
        "text: the comment body inside the quotes after 'saying'."
    ),
}

_DEPLOY_SEED_OFFSET = 900000  # deploy bindings' seeds, disjoint from 0-999


def deploy_params(family: str, seed: int, env) -> dict:
    from .family import instance_params

    return instance_params(family, _DEPLOY_SEED_OFFSET + seed, env=env)


def deploy_uses(family: str, n: int, env, offset: int = 0) -> list:
    """(goal_text, expected_binding, expected_params) per use."""
    from .family import goal_text

    uses = []
    for i in range(n):
        params = deploy_params(family, offset + i, env)
        task = type("T", (), {"params": params})()  # minimal goal carrier
        goal = goal_text(task)
        uses.append((goal, params_to_binding(family, params), params))
    return uses


def _extraction_body(goal_text: str, family: str, extra: str = "") -> str:
    keys = ", ".join(BINDING_FIELDS)
    return f"""Extract the {len(BINDING_FIELDS)} task fields from the request below as a JSON
object with exactly the keys {keys}.

{(_FIELD_HINTS[family]).rstrip()}
{extra}
Request: {goal_text}

Reply with ONLY the JSON object. No markdown fences, no explanation."""


def build_extraction_prompt(goal_text: str, family: str) -> list[dict]:
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": _extraction_body(goal_text, family)},
    ]


def build_retry_prompt(goal_text: str, family: str, errors: list[str]) -> list[dict]:
    extra = (
        "\nYour previous reply was rejected by the type check:\n"
        + "; ".join(errors)
        + "\nFix exactly these problems.\n"
    )
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": _extraction_body(goal_text, family, extra)},
    ]


def binding_type_errors(got, family: str) -> list[str]:
    if not isinstance(got, dict):
        return ["reply is not a JSON object"]
    errors = []
    for field in BINDING_FIELDS:
        value = got.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty string field '{field}'")
        elif len(value) > 300:
            errors.append(f"field '{field}' implausibly long (>300 chars)")
    return errors


def parse_binding_json(reply: str):
    try:
        return json.loads(reply)
    except Exception:  # noqa: BLE001
        return None


def run_single_use(
    program,
    family: str,
    goal: str,
    expected: dict,
    expected_params: dict,
    client,
    model: str,
    runner: ProgramRunner,
) -> dict:
    from .agent import WebAgent, _with_backoff

    t0 = time.time()
    tokens = 0
    cost = 0.0
    retries = 0
    error_type = None
    extracted = None
    calls_detail = []

    def call(messages, kind):
        nonlocal tokens, cost
        response = _with_backoff(
            client.chat.completions.create, model=model, messages=messages, temperature=0.0
        )
        usage = WebAgent._usage(response)
        tokens += (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        cost += usage.get("cost_usd") or 0.0
        calls_detail.append({
            "call": len(calls_detail) + 1,
            "kind": kind,
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_tokens": usage.get("cached_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost_usd": usage.get("cost_usd"),
        })
        return response.choices[0].message.content or ""

    got = parse_binding_json(call(build_extraction_prompt(goal, family), "extract"))
    errors = binding_type_errors(got, family)
    if errors:
        retries = 1
        got = parse_binding_json(call(build_retry_prompt(goal, family, errors), "retry"))
        errors = binding_type_errors(got, family)

    success = False
    if got is None:
        error_type = "extraction_json"
    elif errors:
        error_type = "type_check"
    else:
        extracted = {f: str(got[f]).strip() for f in BINDING_FIELDS}
        outcome = runner.run(program, extracted, family, judge_params=expected_params)
        success = outcome["passed"]
        if not success:
            error_type = "program_error" if outcome["error"] else "oracle_fail"

    return {
        "goal": goal,
        "expected": expected,
        "extracted": extracted,
        "success": success,
        "error_type": error_type,
        "retries": retries,
        "tokens": tokens,
        "cost_usd": round(cost, 8),
        "calls_detail": calls_detail,
        "wall_s": round(time.time() - t0, 2),
    }


def run_deployment(
    program,
    family: str,
    uses: list,
    client,
    model: str,
    runner: ProgramRunner,
    role: str | None = None,
    progress_path: str | None = None,
) -> dict:
    """Run the uses in order. ``progress_path``: when given, every completed
    use is appended to that JSONL file first, and uses already recorded in
    it at start are skipped (a crashed deploy resumes exactly where it
    stopped, with no duplicated extraction calls)."""
    from pathlib import Path

    if client is None:
        client = _openai_client()
    records = []
    if progress_path and Path(progress_path).is_file():
        for line in Path(progress_path).read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
        if records:
            print(f"  deploy resume: {len(records)} use(s) already recorded",
                  flush=True)
    for goal, expected, expected_params in uses[len(records):]:
        record = run_single_use(
            program, family, goal, expected, expected_params, client, model, runner
        )
        records.append(record)
        if progress_path:
            with Path(progress_path).open("a") as fh:
                fh.write(json.dumps(record) + "\n")
                fh.flush()
        tag = record["error_type"] or ("OK" if record["success"] else "FAIL")
        print(f"  use {len(records):>2}: {record['tokens']:>5} tok (r{record['retries']}) {tag}", flush=True)
    tokens = [r["tokens"] for r in records]
    summary = {
        "model": model,
        "family": family,
        "n": len(records),
        "uses": records,
        "success_count": sum(1 for r in records if r["success"]),
        "success_rate": (sum(1 for r in records if r["success"]) / len(records)) if records else None,
        "d_tokens_mean": (sum(tokens) / len(tokens)) if tokens else None,
        "total_tokens": sum(tokens),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 8),
        "record_type": "deploy",
    }
    if role:
        summary["role"] = role
    return summary

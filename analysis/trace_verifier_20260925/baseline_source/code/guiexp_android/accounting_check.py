"""Per-call usage records: their shape, and the guard that a finished cell
has one for every model call it was charged for.

Why this module exists. The cache-adjusted unit of
docs/cache-adjusted-accounting.md section 8 needs, for the i-th call of an
episode, that call's prompt size and the PREVIOUS call's prompt plus
completion. Stage totals cannot supply either, so any stage that keeps only
totals is unaccountable after the fact and has to be re-run. Reactive
episodes always wrote one record per call into ``trajectory.jsonl``; the
stages that call the model directly did not, and :func:`call_record` is the
shape they now write.

The record is deliberately readable two ways, because both readers already
exist in this package:

  * nested, like ``trajectory.jsonl``: ``rec["step"]`` and
    ``rec["usage"]["prompt_tokens"]`` (what :func:`explore.episode_usage`
    folds);
  * flat, like ``build.json``'s ``per_episode`` rows: ``rec["prompt_tokens"]``
    (what :func:`build_protocol.add` folds).

:func:`usage_of` picks whichever is present, so one reader folds both.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.accounting_check \
        experimental-results/guiexp_android/t16_build/z-ai_glm-5.3-flash/ContactsAddContact
"""

from __future__ import annotations

import json
from pathlib import Path

# The four raw numbers every per-call record must carry. ``total_tokens`` is
# derived, so it is written but not required of an input usage dict.
PER_CALL_FIELDS = ("prompt_tokens", "cached_tokens", "completion_tokens", "cost_usd")


def call_record(
    step: int,
    call: int,
    usage: dict,
    stage: str | None = None,
    **extra,
) -> dict:
    """One model call's usage, in the shape every stage persists.

    ``step`` is the step the call produced (1-based, matching
    ``trajectory.jsonl``); ``call`` is the 1-based index of the call within
    the episode, so ``call == 1`` marks the first call -- the one with no
    predecessor, which the cache-adjusted unit charges in full.

    ``cached_tokens`` and ``cost_usd`` are copied verbatim, so a provider that
    reports neither stays distinguishable from one that reports zero (the
    convention ``AndroidAgent._usage`` sets).
    """
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


def usage_of(record: dict) -> dict:
    """The usage numbers of a per-call record, nested or flat."""
    nested = record.get("usage")
    if isinstance(nested, dict) and any(f in nested for f in PER_CALL_FIELDS):
        return nested
    return record


def sum_usage(usages) -> dict:
    """Add several calls into one usage dict of the agent's own shape.

    ``cached_tokens`` and ``cost_usd`` stay ``None`` when no call reported
    them, so a provider that reports neither is still distinguishable from one
    reporting zero.
    """
    total = {"prompt_tokens": 0, "completion_tokens": 0,
             "cached_tokens": None, "cost_usd": None}
    for usage in usages:
        usage = usage or {}
        total["prompt_tokens"] += usage.get("prompt_tokens") or 0
        total["completion_tokens"] += usage.get("completion_tokens") or 0
        for field in ("cached_tokens", "cost_usd"):
            value = usage.get(field)
            if value is not None:
                total[field] = (total[field] or 0) + value
    return total


def fold_calls(records) -> dict:
    """Sum per-call records into the same totals shape the stages report."""
    totals = {f: 0 for f in PER_CALL_FIELDS}
    totals["cost_usd"] = 0.0
    calls = 0
    for record in records:
        usage = usage_of(record)
        calls += 1
        for field in ("prompt_tokens", "cached_tokens", "completion_tokens"):
            totals[field] += usage.get(field) or 0
        totals["cost_usd"] += usage.get("cost_usd") or 0.0
    return {
        "calls": calls,
        "prompt_tokens": totals["prompt_tokens"],
        "cached_tokens": totals["cached_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "total_tokens": totals["prompt_tokens"] + totals["completion_tokens"],
        "cost_usd": round(totals["cost_usd"], 8),
    }


# ------------------------------------------------------------ the cell guard


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _trajectory_calls(path: Path) -> int:
    """Model-call records in a trajectory.jsonl (the final record is not one)."""
    if not path.exists():
        return 0
    calls = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("record_type") == "final":
            continue
        if isinstance(record.get("usage"), dict):
            calls += 1
    return calls


def _has_fields(record) -> bool:
    return isinstance(record, dict) and all(f in usage_of(record) for f in PER_CALL_FIELDS)


def _check_exploration(cell: Path, record: dict, problems: list[str]) -> None:
    stage = record.get("exploration") or {}
    totals = stage.get("totals") or {}
    if not (totals.get("calls") or 0):
        return
    for episode in stage.get("per_episode") or []:
        if episode.get("reused"):
            continue  # its per-call records live in the grid run it reuses
        seed, attempt = episode.get("seed"), episode.get("attempt")
        traj = cell / "explore" / f"s{seed}_a{attempt}" / "trajectory.jsonl"
        if _trajectory_calls(traj) == 0:
            problems.append(
                f"exploration: seed {seed} attempt {attempt} was charged "
                f"{episode.get('model_calls')} calls but {traj} has no per-call records"
            )
    for reflection in stage.get("reflections") or []:
        if not _has_fields(reflection):
            problems.append(
                f"exploration: reflection for seed {reflection.get('seed')} "
                "has no per-call usage fields"
            )
    if (stage.get("reflection_calls") or 0) and not (stage.get("reflections") or []):
        problems.append("exploration: reflection calls were charged but none are recorded")


def _check_translator(cell: Path, record: dict, problems: list[str]) -> None:
    totals = (record.get("translator") or {}).get("totals") or {}
    if not (totals.get("calls") or 0):
        return
    translation = _read_json(cell / "translation.json")
    if translation is None:
        problems.append("translator: charged calls but translation.json is missing or unreadable")
        return
    for seed, result in translation.items():
        steps = result.get("steps") or []
        charged = ((result.get("totals") or {}).get("calls")) or 0
        if charged and len(steps) != charged:
            problems.append(
                f"translator: seed {seed} charged {charged} calls but recorded {len(steps)}"
            )
        for step in steps:
            if not _has_fields(step):
                problems.append(
                    f"translator: seed {seed} step {step.get('step')} has no per-call usage"
                )


def _check_builder(record: dict, problems: list[str]) -> None:
    builder = record.get("builder") or {}
    totals = builder.get("totals_all_calls") or {}
    if not (totals.get("calls") or 0):
        return
    initial = builder.get("initial") or {}
    if not initial:
        problems.append("builder: charged calls but no per-call entries under builder.initial")
    for key, entry in initial.items():
        if not _has_fields(entry):
            problems.append(f"builder: {key} has no per-call usage fields")


def _check_verification(cell: Path, record: dict, problems: list[str]) -> None:
    stage = record.get("verification") or {}
    charged = {
        "analyzer": (stage.get("analyzer") or {}).get("calls") or 0,
        "resume_episodes": (stage.get("resume_episodes") or {}).get("calls") or 0,
        "builder_refinements": (stage.get("builder_refinements") or {}).get("calls") or 0,
    }
    if not any(charged.values()):
        return
    verify = _read_json(cell / "verify.json")
    if verify is None:
        problems.append("verification: charged calls but verify.json is missing or unreadable")
        return
    rounds = verify.get("rounds") or []
    if not rounds:
        problems.append("verification: charged calls but verify.json records no rounds")
        return
    resume_calls = 0
    for index, round_ in enumerate(rounds, start=1):
        analysis = round_.get("analysis") or {}
        if charged["analyzer"] and not _has_fields(analysis):
            problems.append(f"verification: round {index} analyzer call has no usage record")
        if charged["builder_refinements"] and not _has_fields(round_.get("refinement_usage")):
            problems.append(f"verification: round {index} refinement call has no usage record")
        resume = round_.get("resume") or {}
        detail = resume.get("calls_detail")
        expected = resume.get("calls")
        if charged["resume_episodes"]:
            if not detail:
                problems.append(
                    f"verification: round {index} resume episode has no per-call records"
                )
            else:
                resume_calls += len(detail)
                if expected is not None and len(detail) != expected:
                    problems.append(
                        f"verification: round {index} resume charged {expected} calls "
                        f"but recorded {len(detail)}"
                    )
                if not all(_has_fields(c) for c in detail):
                    problems.append(
                        f"verification: round {index} resume records miss usage fields"
                    )
    if charged["resume_episodes"] and resume_calls != charged["resume_episodes"]:
        problems.append(
            f"verification: resume episodes charged {charged['resume_episodes']} calls "
            f"but {resume_calls} per-call records are on disk"
        )


def _check_deploy(cell: Path, record: dict, problems: list[str]) -> None:
    stage = record.get("deploy") or {}
    if stage.get("skipped") or not (stage.get("total_tokens") or 0):
        return
    deploy = _read_json(cell / "deploy.json")
    if deploy is None:
        problems.append("deploy: charged tokens but deploy.json is missing or unreadable")
        return
    for index, use in enumerate(deploy.get("uses") or [], start=1):
        if not (use.get("tokens") or 0):
            continue
        detail = use.get("calls_detail")
        if not detail:
            problems.append(f"deploy: use {index} charged tokens but has no per-call records")
        elif not all(_has_fields(c) for c in detail):
            problems.append(f"deploy: use {index} per-call records miss usage fields")


def _check_doc_arm(cell: Path, record: dict, problems: list[str]) -> None:
    stage = record.get("doc_arm") or {}
    if not ((stage.get("totals") or {}).get("calls") or 0):
        return
    for episode in stage.get("episodes") or []:
        traj = cell / "doc_arm" / f"s{episode.get('seed')}" / "trajectory.jsonl"
        if _trajectory_calls(traj) == 0:
            problems.append(
                f"doc_arm: seed {episode.get('seed')} was charged "
                f"{episode.get('model_calls')} calls but {traj} has no per-call records"
            )


def check_cell(cell_dir: Path | str) -> list[str]:
    """Every stage of a finished cell that was charged model calls must have
    per-call records on disk. Returns the problems, empty when the cell is
    accountable.
    """
    cell = Path(cell_dir)
    record = _read_json(cell / "build.json")
    if record is None:
        return [f"{cell / 'build.json'} is missing or unreadable"]
    problems: list[str] = []
    _check_exploration(cell, record, problems)
    _check_translator(cell, record, problems)
    _check_builder(record, problems)
    _check_verification(cell, record, problems)
    _check_deploy(cell, record, problems)
    _check_doc_arm(cell, record, problems)
    return problems


def warn_if_unaccountable(cell_dir: Path | str) -> list[str]:
    """Run :func:`check_cell` and print a warning; never raises.

    The build driver calls this at the end of a cell. A failed check means the
    run's cache-adjusted unit cannot be recomputed for that stage, which is
    worth shouting about but is not a reason to throw away a finished run.
    """
    try:
        problems = check_cell(cell_dir)
    except Exception as exc:  # noqa: BLE001 - the guard must never sink a run
        print(f"WARNING: accounting check itself failed: {type(exc).__name__}: {exc}", flush=True)
        return [f"{type(exc).__name__}: {exc}"]
    if problems:
        print(f"WARNING: {len(problems)} stage(s) in {cell_dir} have no per-call usage records:",
              flush=True)
        for problem in problems:
            print(f"  - {problem}", flush=True)
    return problems


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cell", nargs="+", help="finished cell directories (the ones with build.json)")
    args = parser.parse_args()
    bad = 0
    for cell in args.cell:
        problems = check_cell(cell)
        bad += 1 if problems else 0
        print(f"{cell}: {'OK' if not problems else str(len(problems)) + ' problem(s)'}")
        for problem in problems:
            print(f"  - {problem}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

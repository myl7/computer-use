"""Stage-2 gate: run a compiled Android program on K held-out bindings.

The Android mirror of ``guiexp/gate_runner.py``: the admission gate for a
candidate family program is K held-out bindings the compile call never saw,
each judged by the family's OWN evaluator (android_world ``is_successful``
-- the contacts content-provider query, the calendar SQLite diff, or the
Markor file check). A program passes the gate iff it passes every binding.

Held-out bindings are seed-deterministic and disjoint from the trajectory's
instance: they are draws of the family's own ``generate_random_params`` on
gate seeds derived from a sha256 namespace ("guiexp_android:gate:<family>:
<seed>") separate from the instance seeds, skipping any draw equal to the
trajectory's instance (and de-duplicated), so a gate seed can never collide
with (or drift against) an instance seed.

Output JSON: {bindings_passed, bindings_total, detail, tokens, cost_usd}
where tokens/cost_usd are the compile price C when this CLI also compiled,
else zero (program reused).

    ../.venv-android/bin/python -m guiexp_android.gate_runner --mock \
        --trajectory experimental-results/guiexp_android/.../trajectory.jsonl \
        --family ContactsAddContact --k 5 --keep-emulator
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import android_env
from .compiler import (
    MockCompiler,
    binding_fields,
    compile_trajectory,
    load_trajectory,
    params_to_binding,
    trajectory_instance,
)
from .program_runtime import ProgramRunner, program_from_path, program_from_source
from .program_runtime import Program  # re-exported for type annotations

GATE_SEED_DEFAULT = 0
GATE_K = 5  # held-out bindings per gate

GATE_MIN_PASS = 4
"""Admission threshold: a candidate program is admitted iff it passes at
least this many of the ``GATE_K`` held-out bindings.

The gate has five bindings, so its resolution is one fifth: the observable
pass rates are 0, 0.2, 0.4, 0.6, 0.8 and 1.0, and no finer distinction than
0.2 exists. Four of five is the lowest rate that is still above the coin
flip by more than one binding's worth of resolution, so it is where the line
goes. A program at 4/5 is admitted and deployed; a program at 3/5 is not,
and the cell is reported as having produced nothing deployable.

The threshold is the admission criterion for deployment, and the only one:
the AutoRPA verification loop's own ``unautomatable`` verdict is reported
but does not decide anything.
"""


def heldout_bindings(
    family: str,
    k: int = 5,
    exclude_params: dict | None = None,
    seed: int = GATE_SEED_DEFAULT,
    max_draws: int = 200,
) -> list[dict]:
    """K seed-deterministic held-out draws, disjoint from the instance.

    Returns [{"params": <full generate_random_params draw>, "binding":
    <program-facing fields>}]. Deterministic: the gate seed hashes into the
    namespace above, and successive seeds walk forward until K distinct
    bindings not equal to ``exclude_params`` are found.
    """
    digest = hashlib.sha256(f"guiexp_android:gate:{family}:{seed}".encode()).digest()
    gate_seed = int.from_bytes(digest[:8], "big")
    draws: list[dict] = []
    seen_bindings: list[dict] = []
    offset = 0
    while len(draws) < k and offset < max_draws:
        params = android_env.instance_params(family, gate_seed + offset)
        binding = params_to_binding(family, params)
        offset += 1
        if exclude_params is not None and params == exclude_params:
            continue
        if binding in seen_bindings:
            continue
        seen_bindings.append(binding)
        draws.append({"params": params, "binding": binding})
    if len(draws) < k:
        raise ValueError(
            f"could not draw {k} distinct held-out bindings for {family} "
            f"within {max_draws} seeds"
        )
    return draws


def run_gate(program: Program, family: str, draws: list[dict], runner: ProgramRunner) -> dict:
    """Every held-out binding against the real device; judge = the family's
    own ``is_successful`` oracle."""
    detail: list[dict] = []
    for draw in draws:
        binding = draw["binding"]
        missing = [
            field
            for field in binding_fields(family)
            if not str(binding.get(field, "")).strip()
        ]
        if missing:
            raise ValueError(f"binding is missing non-empty values: {binding}")
        outcome = runner.run(program, binding, family, judge_params=draw["params"])
        detail.append({"binding": binding, "passed": outcome["passed"], "error": outcome["error"]})
    return {
        "bindings_passed": sum(1 for d in detail if d["passed"]),
        "bindings_total": len(detail),
        "detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True, help="task family (validated below; avoids android_world import for --help)")
    parser.add_argument("--trajectory", default=None, help="trajectory.jsonl to compile from")
    parser.add_argument("--program", default=None, help="existing compiled program .py (skips compiling)")
    parser.add_argument("--mock", action="store_true", help="compile with the deterministic MockCompiler")
    parser.add_argument("--model", default=None, help="OpenRouter model id for a real compile")
    parser.add_argument("--annotate", action="store_true",
                        help="shadow-replay the trajectory to attach element text/hints to the compile prompt")
    parser.add_argument("--k", type=int, default=5, help="number of held-out bindings")
    parser.add_argument("--gate-seed", type=int, default=GATE_SEED_DEFAULT)
    parser.add_argument("--out", default=None, help="output JSON path (default under experimental-results/guiexp_android/)")
    parser.add_argument("--keep-emulator", action="store_true",
                        help="do not shut down an emulator this run booted")
    args = parser.parse_args()

    from .conditions import FAMILIES

    if args.family not in FAMILIES:
        parser.error(f"unknown family {args.family!r}")
    if not args.program and not args.trajectory:
        parser.error("need --trajectory (to compile) or --program (already compiled)")
    if args.program and (args.mock or args.model):
        parser.error("--program skips compiling; drop --mock/--model")

    compile_usage: dict = {}
    compile_cost = 0.0
    model = "reused"
    trajectory_path = None
    instance = None
    env = android_env.AndroidWorldEnv()
    try:
        if args.program:
            _module, program = program_from_path(args.program)
            program_path = str(Path(args.program).resolve())
        else:
            if args.mock and args.model:
                parser.error("--mock and --model are mutually exclusive")
            from .compiler import annotate_trajectory

            trajectory_path = Path(args.trajectory)
            _steps, final = load_trajectory(trajectory_path, args.family)
            instance = trajectory_instance(args.family, final)
            annotations = None
            if args.annotate:
                # same env: the shadow replay boots/reuses the emulator the
                # gate itself will run on
                annotations = annotate_trajectory(trajectory_path, args.family, env=env)
            if args.mock:
                model = "mock"
                result = MockCompiler().compile(trajectory_path, args.family, annotations)
            else:
                model = args.model or "openai/gpt-4o-mini"
                result = compile_trajectory(model, trajectory_path, args.family, annotations)
            compile_usage = result["usage"]
            compile_cost = result["cost_usd"] or 0.0
            out_dir = Path(args.out).parent if args.out else (
                android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
                / f"gate_{model.replace('/', '-')}_{args.family}"
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            program_path = str(out_dir / "family_program.py")
            Path(program_path).write_text(result["program_source"])
            _module, program = program_from_source(result["program_source"])

        bindings = heldout_bindings(args.family, args.k, exclude_params=instance, seed=args.gate_seed)
        assert instance is None or all(d["params"] != instance for d in bindings)  # disjointness
        gate = run_gate(program, args.family, bindings, ProgramRunner(env))
    finally:
        env.close()
        if not args.keep_emulator:
            env.stop_emulator()  # adb emu kill, only if we booted it

    tokens = (compile_usage.get("prompt_tokens") or 0) + (compile_usage.get("completion_tokens") or 0)
    result = {
        "family": args.family,
        "model": model,
        "program_path": program_path,
        "trajectory": str(trajectory_path) if trajectory_path else None,
        "instance": params_to_binding(args.family, instance) if instance is not None else None,
        "gate_seed": args.gate_seed,
        "bindings_passed": gate["bindings_passed"],
        "bindings_total": gate["bindings_total"],
        "detail": gate["detail"],
        "tokens": tokens,
        "cost_usd": compile_cost,
        "compile_usage": compile_usage or None,
        "record_type": "gate",
    }
    out = Path(args.out) if args.out else (
        android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
        / f"gate_{model.replace('/', '-')}_{args.family}" / "gate.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    for d in gate["detail"]:
        label = next(iter(d["binding"].values()))
        tag = "OK" if d["passed"] else f"FAIL {d['error'] or 'oracle mismatch'}"
        print(f"  {str(label):<28} {tag}")
    print(f"gate: {gate['bindings_passed']}/{gate['bindings_total']} bindings pass "
          f"(compile {tokens} tok, ${compile_cost:.6f})")
    print(f"wrote {out}")
    return 0 if gate["bindings_passed"] == gate["bindings_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

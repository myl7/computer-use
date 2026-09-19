"""Stage-2 gate: run a compiled program on K held-out bindings.

Conventions follow openapps-exp/compile_gate.py: the admission gate for a
candidate family program is N held-out bindings the compile call never saw,
each judged by the environment's own six-field oracle. A program passes the
gate iff it passes EVERY binding.

Held-out bindings are seed-deterministic and disjoint from the trajectory's
instance: they come from a dedicated pool (below, the same five bindings the
old harness gated on, so numbers stay comparable), selected by a sha256
hash over a gate seed in a namespace ("guiexp:gate:<seed>") separate from
the instance seeds ("guiexp:<seed>"), skipping anything equal to the
trajectory's instance.

Output JSON: {bindings_passed, detail: [{binding, passed, error}], tokens,
cost_usd} where tokens/cost_usd are the compile price C when this CLI also
compiled, else zero (program reused).

    ../.venv-gui/bin/python -m guiexp.gate_runner --mock \
        --trajectory experimental-results/guiexp/wizard_discover_s0_mock/trajectory.jsonl \
        --layout wizard --k 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .compiler import MockCompiler, compile_trajectory, load_trajectory, trajectory_instance
from .env import FIELDS
from .program_runtime import BindingRunner, program_from_path, program_from_source
from .program_runtime import Program  # re-exported for type annotations
from .runner import DEFAULT_OUT_ROOT

# The held-out pool: never shown to the compile call. Same five bindings as
# openapps-exp's compile_gate.py; disjoint from env.INSTANCE_POOL's values.
GATE_BINDING_POOL = [
    dict(title="Alpha Review", date="2026-05-04", description="Sprint retro",
         location="Room 1", url="https://example.com/alpha", invitees="Carol"),
    dict(title="Beta Sync", date="2026-06-11", description="Quarterly planning",
         location="Room 7", url="https://example.com/beta", invitees="Dave"),
    dict(title="Gamma Demo", date="2026-07-21", description="Product walkthrough",
         location="Hall B", url="https://example.com/gamma", invitees="Erin"),
    dict(title="Delta Audit", date="2026-08-02", description="Security review",
         location="Suite 900", url="https://example.com/delta", invitees="Frank"),
    dict(title="Epsilon Workshop", date="2026-09-15", description="Team offsite",
         location="Main Hall", url="https://example.com/epsilon", invitees="Grace"),
]

GATE_SEED_DEFAULT = 0


def heldout_bindings(k: int = 5, exclude: dict | None = None, seed: int = GATE_SEED_DEFAULT) -> list[dict]:
    """K seed-deterministic held-out bindings, disjoint from ``exclude``.

    Determinism uses the same sha256 trick as env.instance_for_seed but over
    the "guiexp:gate:" namespace, so a gate seed can never collide with (or
    drift against) an instance seed.
    """
    pool = [b for b in GATE_BINDING_POOL if not exclude or b != exclude]
    if k > len(pool):
        raise ValueError(f"asked for {k} held-out bindings, only {len(pool)} available")
    digest = hashlib.sha256(f"guiexp:gate:{seed}".encode()).digest()
    start = int.from_bytes(digest[:8], "big") % len(pool)
    ordered = [pool[(start + i) % len(pool)] for i in range(len(pool))]
    return [dict(b) for b in ordered[:k]]


def run_gate(
    program: Program,
    bindings: list[dict],
    base_url: str,
    layout: str,
    headless: bool = True,
) -> dict:
    """Every binding against the real app; judge = the env's six-field oracle."""
    detail: list[dict] = []
    with BindingRunner(base_url, layout, headless=headless) as runner:
        for binding in bindings:
            if any(not isinstance(binding.get(f), str) or not binding[f] for f in FIELDS):
                raise ValueError(f"binding is not six non-empty strings: {binding}")
            outcome = runner.run(program, binding)
            detail.append({"binding": binding, "passed": outcome["passed"], "error": outcome["error"]})
    return {
        "bindings_passed": sum(1 for d in detail if d["passed"]),
        "bindings_total": len(detail),
        "detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trajectory", default=None, help="trajectory.jsonl to compile from")
    parser.add_argument("--program", default=None, help="existing compiled program .py (skips compiling)")
    parser.add_argument("--mock", action="store_true", help="compile with the deterministic MockCompiler")
    parser.add_argument("--model", default=None, help="OpenRouter model id for a real compile")
    parser.add_argument("--layout", default="wizard")
    parser.add_argument("--k", type=int, default=5, help="number of held-out bindings")
    parser.add_argument("--gate-seed", type=int, default=GATE_SEED_DEFAULT)
    parser.add_argument("--out", default=None, help="output JSON path (default under experimental-results/guiexp/)")
    parser.add_argument("--show", action="store_true", help="run Chromium headed")
    args = parser.parse_args()

    if not args.program and not args.trajectory:
        parser.error("need --trajectory (to compile) or --program (already compiled)")
    if args.program and (args.mock or args.model):
        parser.error("--program skips compiling; drop --mock/--model")

    from .app_server import LAYOUTS, AppServer

    if args.layout not in LAYOUTS:
        parser.error(f"unknown layout {args.layout!r}")

    compile_usage: dict = {}
    compile_cost = 0.0
    model = "reused"
    trajectory_path = None
    instance = None
    if args.program:
        _module, program = program_from_path(args.program)
        program_path = str(Path(args.program).resolve())
    else:
        if args.mock and args.model:
            parser.error("--mock and --model are mutually exclusive")
        trajectory_path = Path(args.trajectory)
        _steps, final = load_trajectory(trajectory_path)
        instance = trajectory_instance(final)
        server = AppServer(args.layout)
        base_url = server.start()
        try:
            if args.mock:
                model = "mock"
                result = MockCompiler().compile(trajectory_path, layout=args.layout)
            else:
                model = args.model or "openai/gpt-4o-mini"
                result = compile_trajectory(model, trajectory_path, layout=args.layout)
        finally:
            server.stop()  # gate reuses/starts it below via the registry
        compile_usage = result["usage"]
        compile_cost = result["cost_usd"] or 0.0
        out_dir = Path(args.out).parent if args.out else (
            DEFAULT_OUT_ROOT / f"gate_{model.replace('/', '-')}_{args.layout}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        program_path = str(out_dir / "family_program.py")
        Path(program_path).write_text(result["program_source"])
        _module, program = program_from_source(result["program_source"])

    bindings = heldout_bindings(args.k, exclude=instance, seed=args.gate_seed)
    assert instance is None or all(b != instance for b in bindings)  # disjointness

    server = AppServer(args.layout)
    base_url = server.start()
    try:
        gate = run_gate(program, bindings, base_url, args.layout, headless=not args.show)
    finally:
        server.stop()

    tokens = (compile_usage.get("prompt_tokens") or 0) + (compile_usage.get("completion_tokens") or 0)
    result = {
        "layout": args.layout,
        "model": model,
        "program_path": program_path,
        "trajectory": str(trajectory_path) if trajectory_path else None,
        "instance": instance,
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
        DEFAULT_OUT_ROOT / f"gate_{model.replace('/', '-')}_{args.layout}" / "gate.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    for d in gate["detail"]:
        tag = "OK" if d["passed"] else f"FAIL {d['error'] or 'oracle mismatch'}"
        print(f"  {d['binding']['title']:<20} {tag}")
    print(f"gate: {gate['bindings_passed']}/{gate['bindings_total']} bindings pass "
          f"(compile {tokens} tok, ${compile_cost:.6f})")
    print(f"wrote {out}")
    return 0 if gate["bindings_passed"] == gate["bindings_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

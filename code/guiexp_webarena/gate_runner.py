"""Stage gate: run a compiled program on K held-out bindings (sha256 namespace).

The WebArena mirror of guiexp_android/gate_runner.py, scheme copied
verbatim: held-out bindings are seed-deterministic draws over the sha256
namespace "guiexp_webarena:gate:<family>:<seed>", skipping the building
instances' params and duplicates. GATE_K=5, admission >= 4/5.
"""

from __future__ import annotations

import hashlib
import random

from .family import (
    FAMILY,
    binding_fields,
    params_to_binding,
)
from .program_runtime import ProgramRunner, program_from_source

GATE_SEED_DEFAULT = 0
GATE_K = 5
GATE_MIN_PASS = 4

# Binding seeds already consumed by the building episodes (1,2,3), the
# deploy uses and the doc arm (4,5,6) must stay disjoint from gate draws.
# The gate draws its OWN seeds from the sha256 namespace, so disjointness
# holds by construction; exclude_params is checked against all known
# instance params anyway.


def gate_seed_for(family: str, seed: int = GATE_SEED_DEFAULT) -> int:
    digest = hashlib.sha256(f"guiexp_webarena:gate:{family}:{seed}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def heldout_bindings(
    family: str,
    k: int = GATE_K,
    exclude_params: list | None = None,
    seed: int = GATE_SEED_DEFAULT,
    env=None,
    max_draws: int = 200,
) -> list[dict]:
    """K seed-deterministic held-out draws, disjoint from the given params.

    Each draw is a full instance_params draw on a gate seed (base + offset
    walk), de-duplicated on the binding and skipped when it equals any
    excluded instance (or its binding).
    """
    from .family import instance_params

    if family != FAMILY:
        raise ValueError(f"unknown family {family!r}")
    base = gate_seed_for(family, seed)
    exclude_params = exclude_params or []
    draws: list[dict] = []
    seen: list[dict] = []
    offset = 0
    while len(draws) < k and offset < max_draws:
        params = instance_params(family, base + offset, env=env)
        binding = params_to_binding(family, params)
        offset += 1
        if any(params == ex or binding == params_to_binding(family, ex) for ex in exclude_params):
            continue
        if binding in seen:
            continue
        seen.append(binding)
        draws.append({"params": params, "binding": binding})
    if len(draws) < k:
        raise ValueError(f"could not draw {k} distinct held-out bindings within {max_draws} seeds")
    return draws


def run_gate(program, family: str, draws: list[dict], runner: ProgramRunner) -> dict:
    detail = []
    for draw in draws:
        binding = draw["binding"]
        missing = [f for f in binding_fields(family) if not str(binding.get(f, "")).strip()]
        if missing:
            raise ValueError(f"binding is missing non-empty values: {binding}")
        outcome = runner.run(program, binding, family, judge_params=draw["params"])
        detail.append({"binding": binding, "passed": outcome["passed"], "error": outcome["error"]})
    return {
        "bindings_passed": sum(1 for d in detail if d["passed"]),
        "bindings_total": len(detail),
        "detail": detail,
    }


def gate_from_source(source: str, family: str, env, exclude_params: list | None = None) -> dict:
    """Compile-free convenience: gate one program source on the standard draw."""
    _module, program = program_from_source(source)
    draws = heldout_bindings(family, exclude_params=exclude_params, env=env)
    return run_gate(program, family, draws, ProgramRunner(env))

"""Stage gate: run a compiled program on K held-out bindings.

Ported from guiexp_android/gate_runner.py. Held-out bindings are
seed-deterministic draws of the family generator over a sha256 namespace
("guiexp_osworld:gate:<family>:<seed>"), disjoint from the building seeds'
instances (de-duplicated), judged by the family's own checker.
"""

from __future__ import annotations

import hashlib

from . import families, guest_env
from .compiler import binding_fields, params_to_binding
from .program_runtime import Program, ProgramRunner

GATE_SEED_DEFAULT = 0
GATE_K = 5
GATE_MIN_PASS = 4  # admission threshold (the Android arm's ruling, unchanged)


def heldout_bindings(
    family: str,
    k: int = 5,
    exclude_params: list[dict] | dict | None = None,
    seed: int = GATE_SEED_DEFAULT,
    max_draws: int = 200,
) -> list[dict]:
    """K seed-deterministic held-out draws, disjoint from the building
    instances (exclude_params may be one draw or a list of them)."""
    digest = hashlib.sha256(f"guiexp_osworld:gate:{family}:{seed}".encode()).digest()
    gate_seed = int.from_bytes(digest[:8], "big")
    excludes = []
    if exclude_params is not None:
        excludes = exclude_params if isinstance(exclude_params, list) else [exclude_params]
    draws: list[dict] = []
    seen: list[dict] = []
    offset = 0
    while len(draws) < k and offset < max_draws:
        params = families.instance_params(family, gate_seed + offset)
        binding = params_to_binding(family, params)
        offset += 1
        if params in excludes:
            continue
        if binding in seen:
            continue
        seen.append(binding)
        draws.append({"params": params, "binding": binding})
    if len(draws) < k:
        raise ValueError(f"could not draw {k} distinct held-out bindings for {family}")
    return draws


def run_gate(program: Program, family: str, draws: list[dict],
             runner: ProgramRunner) -> dict:
    detail: list[dict] = []
    for draw in draws:
        binding = draw["binding"]
        missing = [f for f in binding_fields(family)
                   if not str(binding.get(f, "")).strip()]
        if missing:
            raise ValueError(f"binding is missing non-empty values: {binding}")
        outcome = runner.run(program, binding, family, judge_params=draw["params"])
        detail.append({"binding": binding, "passed": outcome["passed"],
                       "error": outcome["error"]})
    return {
        "bindings_passed": sum(1 for d in detail if d["passed"]),
        "bindings_total": len(detail),
        "detail": detail,
    }

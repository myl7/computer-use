"""Offline zero-model control using two explicit public-goal templates.

The parser is intentionally narrow.  It binds values from the goal string,
then checks a parameterized prefix against recorded public states and actions.
It never executes a UI action, calls a model, reads an oracle, or learns from
held-out actions.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import selective_literal_control as literal_control
from . import selective_plan_contract, selective_trace_compatibility

VERSION = "template_v1"
SCHEMA = "selective-template-control/1"
NAMESPACE = "selective_20260915"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ORIGIN = REPO_ROOT / "experimental-results" / "guiexp_android" / NAMESPACE
DEFAULT_OUT = DEFAULT_ORIGIN / "offline_controls" / VERSION

CREATE = "MarkorCreateNote"
DELETE = "MarkorDeleteNote"
FAMILIES = (CREATE, DELETE)
PREFIX_LENGTHS = {CREATE: 3, DELETE: 4}
GOAL_TEMPLATES = {
    DELETE: "Delete the note in Markor named <name>.",
    CREATE: "Create a new note in Markor named <filename> with the following text: <body>",
}
GOAL_PATTERNS = {
    DELETE: re.compile(r"\ADelete the note in Markor named (?P<name>[^\r\n]+)\.\Z"),
    CREATE: re.compile(
        r"\ACreate a new note in Markor named (?P<filename>[^\r\n]+?) "
        r"with the following text: (?P<body>[^\r\n]+)\Z"
    ),
}

# These are exactly the corresponding entries from the preserved literal
# control.  No development or validation trace is used to choose them.
TRAINING_TRACES = tuple(
    item for item in literal_control.TRAINING_TRACES if item["family"] in FAMILIES
)
HELDOUT_TRACES = tuple(
    item
    for item in literal_control.HELDOUT_TRACES
    if item["family"] in FAMILIES
    and (
        (item["family"] == CREATE and item["version"] == "projected_v11_create_dense")
        or (item["family"] == DELETE and item["version"] == "projected_v6_schema")
    )
)


class ControlStop(RuntimeError):
    """Fail-closed error for missing or malformed public inputs."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256(path.read_bytes())
    except OSError as exc:
        raise ControlStop(f"required public artifact is unreadable: {path}") from exc


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError as exc:
        raise ControlStop(f"artifact is outside the frozen workspace: {path}") from exc


def _slot(name: str) -> dict[str, str]:
    return {"slot": name, "transform": "identity"}


def _stem(filename: str) -> str:
    leaf = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in leaf or (leaf.startswith(".") and leaf.count(".") == 1):
        return leaf
    return leaf[: leaf.rfind(".")]


def parse_goal(family: str, goal_text: str) -> dict[str, str] | None:
    """Parse one exact public template, or abstain on any mismatch."""

    pattern = GOAL_PATTERNS.get(family)
    if pattern is None or not isinstance(goal_text, str):
        return None
    match = pattern.fullmatch(goal_text)
    if match is None:
        return None
    values = {name: value for name, value in match.groupdict().items()}
    if family == CREATE:
        values["filename_stem"] = _stem(values["filename"])
        if not values["filename_stem"]:
            return None
    else:
        values["file_label"] = f"File {values['name']} "
    return values


def _replace_exact(value: Any, replacements: Mapping[str, Any]) -> tuple[Any, int]:
    """Recursively replace exact literal values while preserving all others."""

    if isinstance(value, str):
        if value in replacements:
            return copy.deepcopy(replacements[value]), 1
        return value, 0
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        count = 0
        for key, child in value.items():
            result[key], changed = _replace_exact(child, replacements)
            count += changed
        return result, count
    if isinstance(value, list):
        result = []
        count = 0
        for child in value:
            updated, changed = _replace_exact(child, replacements)
            result.append(updated)
            count += changed
        return result, count
    return copy.deepcopy(value), 0


def _source_manifest() -> dict[str, Any]:
    modules = {
        "control": Path(__file__).resolve(),
        "literal_control": Path(literal_control.__file__).resolve(),
        "trace_compatibility": Path(selective_trace_compatibility.__file__).resolve(),
        "plan_contract": Path(selective_plan_contract.__file__).resolve(),
        "ax_parser": REPO_ROOT / "computer-use" / "guiexp_android" / "selective_ax_parser.py",
        "evidence_store": REPO_ROOT / "computer-use" / "guiexp_android" / "selective_evidence_store.py",
        "prefix_contract": REPO_ROOT / "computer-use" / "guiexp_android" / "selective_prefix.py",
        "runtime": REPO_ROOT / "computer-use" / "guiexp_android" / "selective_runtime.py",
    }
    hashes = {}
    for name, path in modules.items():
        if not path.is_file():
            raise ControlStop(f"required source is missing: {name}")
        hashes[name] = {"path": _relative(path), "sha256": _sha256_file(path)}
    return {
        "source_sha256": hashes,
        "python": sys.version,
        "platform": platform.platform(),
        "working_directory": "computer-use",
        "interpreter": "../.venv-android/bin/python",
    }


def _checker_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "prefix_steps_matched": report.get("prefix_steps_matched"),
        "first_divergence": copy.deepcopy(report.get("first_divergence")),
        "counterfactual_ui_success": None,
        "task_success_evaluated": False,
        "generalization_proof": False,
        "ui_outcome": None,
    }


def build_parameterized_plan(
    family: str, training_demo: Mapping[str, Any]
) -> dict[str, Any]:
    """Freeze one literal training prefix after exact goal-driven replacement."""

    if family not in FAMILIES:
        raise ControlStop(f"unsupported template family: {family}")
    bindings = parse_goal(family, str(training_demo.get("goal_text") or ""))
    if bindings is None:
        raise ControlStop(f"training goal does not match the frozen {family} template")
    built = literal_control.build_literal_prefix(training_demo)
    candidate = built.get("candidate")
    if not isinstance(candidate, Mapping) or not isinstance(candidate.get("plan"), Mapping):
        raise ControlStop(f"literal training plan unavailable for {family}")
    requested = PREFIX_LENGTHS[family]
    literal_plan = copy.deepcopy(dict(candidate["plan"]))
    literal_length = len(literal_plan.get("steps") or [])
    if literal_length < requested:
        raise ControlStop(f"literal training prefix is shorter than {requested}: {family}")
    literal_plan["steps"] = list(literal_plan["steps"])[:requested]
    if family == CREATE:
        replacements = {bindings["filename_stem"]: _slot("filename_stem")}
        slots = {"filename_stem": "the filename stem supplied by the exact public goal"}
        truncation = "truncate before source step 4, which types .txt and records .txt.md"
    else:
        old_label = f"File {bindings['name']} "
        replacements = {old_label: _slot("file_label")}
        slots = {"file_label": "the observed File wrapper plus the goal name"}
        truncation = None
    plan, replacement_count = _replace_exact(literal_plan, replacements)
    plan["slots"] = slots
    validation = selective_plan_contract.validate_plan(plan)
    if validation.get("valid") is not True:
        raise ControlStop(f"parameterized plan rejected for {family}: {validation.get('errors')}")
    return {
        "family": family,
        "goal_text": str(training_demo["goal_text"]),
        "bindings": bindings,
        "literal_prefix_length": built.get("prefix_length"),
        "prefix_length": requested,
        "replacement_literals": sorted(replacements),
        "replacement_count": replacement_count,
        "truncation": truncation,
        "plan": plan,
    }


def _trace_path(origin: Path, relative_path: str) -> Path:
    return literal_control._trace_path(origin, relative_path)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _exclusive_output(path: Path) -> None:
    if path.exists():
        raise ControlStop(f"offline control output already exists; refusing overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError as exc:
        raise ControlStop(f"offline control output was created concurrently: {path}") from exc


def run_control(origin: Path | str = DEFAULT_ORIGIN, out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    origin = Path(origin).resolve()
    out = Path(out).resolve()
    manifest_path = origin / "training_manifest.json"
    if not origin.is_dir() or not manifest_path.is_file():
        raise ControlStop(f"public selective origin is missing: {origin}")
    training_manifest_hash = _sha256_file(manifest_path)
    training: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for item in TRAINING_TRACES:
        path = _trace_path(origin, item["relative_path"])
        training[item["family"]] = literal_control.load_public_demo(path, item["family"])

    # Freeze the parser, plans, and their source hashes before loading any held-out trace.
    frozen = {
        family: build_parameterized_plan(family, training[family][0]) for family in FAMILIES
    }
    frozen_sources = _source_manifest()
    plan_payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "parser_config": {
            "templates": GOAL_TEMPLATES,
            "patterns": {family: pattern.pattern for family, pattern in GOAL_PATTERNS.items()},
            "matching": "anchored fullmatch; abstain on mismatch",
            "binding_source": "public goal_text only",
        },
        "families": {
            family: {
                key: copy.deepcopy(value)
                for key, value in frozen[family].items()
                if key not in {"plan"} 
            }
            | {"plan": frozen[family]["plan"]}
            for family in FAMILIES
        },
    }
    plan_payload_sha256 = _sha256(_canonical(plan_payload).encode())

    heldout_results: list[dict[str, Any]] = []
    for item in HELDOUT_TRACES:
        path = _trace_path(origin, item["relative_path"])
        demo, metadata = literal_control.load_public_demo(path, item["family"])
        bindings = parse_goal(item["family"], demo["goal_text"])
        result: dict[str, Any] = {
            **metadata,
            "version": item["version"],
            "goal_text": demo["goal_text"],
            "binding_source": "goal_text_only",
            "bindings": bindings,
            "task_success_evaluated": False,
            "ui_outcome": None,
        }
        if bindings is None:
            result.update({"status": "abstained", "abstention_reason": "goal_template_mismatch"})
        else:
            report = selective_trace_compatibility.check_prefix(
                frozen[item["family"]]["plan"], bindings, demo["steps"]
            )
            result.update({"status": report.get("status"), "checker": report, "compatibility": _checker_summary(report)})
        heldout_results.append(result)

    training_results = []
    for family in FAMILIES:
        item = frozen[family]
        report = selective_trace_compatibility.check_prefix(
            item["plan"], item["bindings"], training[family][0]["steps"]
        )
        training_results.append(
            {
                "family": family,
                "path": training[family][1]["path"],
                "goal_text": item["goal_text"],
                "literal_prefix_length": item["literal_prefix_length"],
                "parameterized_prefix_length": item["prefix_length"],
                "replacement_count": item["replacement_count"],
                "compatibility": _checker_summary(report),
                "task_success_evaluated": False,
                "ui_outcome": None,
            }
        )

    results = {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "complete",
        "control_scope": "recorded_trajectory_compatibility",
        "training": training_results,
        "heldout": heldout_results,
        "diagnostic_only": {
            "training_prefix_lengths": {family: PREFIX_LENGTHS[family] for family in FAMILIES},
            "literal_training_prefix_lengths": {family: frozen[family]["literal_prefix_length"] for family in FAMILIES},
            "heldout_compatibility_only": True,
            "ui_outcome": None,
            "task_success_evaluated": False,
            "generalization_proof": False,
        },
        "model_calls": 0,
        "ui_actions": 0,
        "oracle_inputs": [],
        "private_inputs": [],
        "task_success_evaluated": False,
    }
    source_payload = dict(frozen_sources)
    source_payload["training_manifest"] = {"path": _relative(manifest_path), "sha256": training_manifest_hash}
    source_payload["public_trace_sha256"] = {
        row["path"]: row["sha256"] for row in heldout_results
    } | {training[family][1]["path"]: training[family][1]["sha256"] for family in FAMILIES}
    result_payload = json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    config = {
        "schema": SCHEMA,
        "version": VERSION,
        "namespace": NAMESPACE,
        "origin": str(origin),
        "origin_relative": _relative(origin),
        "goal_templates": GOAL_TEMPLATES,
        "parser_config": plan_payload["parser_config"],
        "freeze": "parameterized plans and parser config constructed before heldout load",
        "training_trace_count": len(TRAINING_TRACES),
        "heldout_trace_count": len(HELDOUT_TRACES),
        "prefix_policy": {family: {"steps": PREFIX_LENGTHS[family], "source": "literal training prefix"} for family in FAMILIES},
        "model_calls": 0,
        "ui_actions": 0,
        "oracle_inputs": False,
        "private_inputs": False,
        "plan_file": "plan.json",
        "source_manifest_file": "source_manifest.json",
        "results_file": "results.json",
        "plan_sha256": plan_payload_sha256,
        "source_manifest_sha256": _sha256(_canonical(source_payload).encode()),
        "results_sha256": _sha256(result_payload.encode()),
    }
    _exclusive_output(out)
    _write_json(out / "plan.json", plan_payload)
    _write_json(out / "source_manifest.json", source_payload)
    (out / "results.json").write_text(result_payload, encoding="utf-8")
    _write_json(out / "config.json", config)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true", help="run the bounded offline control")
    parser.add_argument("--origin", type=Path, default=DEFAULT_ORIGIN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required")
    try:
        result = run_control(args.origin, args.out)
    except ControlStop as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output": str(Path(args.out).resolve()), "heldout": len(result["heldout"])}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CREATE",
    "DEFAULT_ORIGIN",
    "DEFAULT_OUT",
    "DELETE",
    "FAMILIES",
    "GOAL_PATTERNS",
    "GOAL_TEMPLATES",
    "HELDOUT_TRACES",
    "PREFIX_LENGTHS",
    "SCHEMA",
    "TRAINING_TRACES",
    "VERSION",
    "ControlStop",
    "build_parameterized_plan",
    "parse_goal",
    "run_control",
]

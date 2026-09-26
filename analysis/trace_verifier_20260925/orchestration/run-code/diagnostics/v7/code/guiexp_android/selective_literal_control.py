"""Offline zero-model trace-lift control for the selective prefix metric.

This control is deliberately literal.  It reads only three preserved public
training traces, chooses the first unique observed selector for each target,
adds the first unique effect-specific post-only visible value as the after
guard, and stops at the first missing witness.  It never calls a model,
executes a UI action, reads private bindings, or consults a task oracle.
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

from . import (
    selective_ax_parser,
    selective_evidence_store,
    selective_guard_evidence,
    selective_plan_contract,
    selective_prefix,
    selective_runtime,
)

VERSION = "literal_v1"
SCHEMA = "selective-literal-control/1"
NAMESPACE = "selective_20260915"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ORIGIN = REPO_ROOT / "experimental-results" / "guiexp_android" / NAMESPACE
DEFAULT_OUT = DEFAULT_ORIGIN / "offline_controls" / VERSION

# These are the three successful fresh public traces selected for the prior
# in-memory diagnostic.  The manifest and raw files are hashed below; no
# success/oracle fields are read from the manifest.
TRAINING_TRACES: tuple[dict[str, str], ...] = (
    {
        "family": "MarkorCreateNote",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/"
            "training/MarkorCreateNote/s915102_a1/trajectory.jsonl"
        ),
    },
    {
        "family": "FilesMoveFile",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/"
            "training/FilesMoveFile/s915101_a0/trajectory.jsonl"
        ),
    },
    {
        "family": "MarkorDeleteNote",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/"
            "training/MarkorDeleteNote/s915101_a0/trajectory.jsonl"
        ),
    },
)

# Existing public serving traces provide a small held-out literal mismatch
# check.  The comparison is against recorded action/observation evidence only;
# it does not interpret their final status or claim a UI task failure.
HELDOUT_TRACES: tuple[dict[str, str], ...] = (
    {
        "family": "MarkorCreateNote",
        "version": "projected_v11_create_dense",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v11_create_dense/episodes/selective_20260915/"
            "projected_v11_create_dense/MarkorCreateNote/b01/full_fallback/"
            "trajectory.jsonl"
        ),
    },
    {
        "family": "MarkorCreateNote",
        "version": "projected_v11_create_dense",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v11_create_dense/episodes/selective_20260915/"
            "projected_v11_create_dense/MarkorCreateNote/b02/full_fallback/"
            "trajectory.jsonl"
        ),
    },
    {
        "family": "FilesMoveFile",
        "version": "projected_v12_files_dense",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v12_files_dense/episodes/selective_20260915/"
            "projected_v12_files_dense/FilesMoveFile/b02/reactive/trajectory.jsonl"
        ),
    },
    {
        "family": "FilesMoveFile",
        "version": "projected_v12_files_dense",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v12_files_dense/episodes/selective_20260915/"
            "projected_v12_files_dense/FilesMoveFile/b02/local_rejoin/trajectory.jsonl"
        ),
    },
    {
        "family": "MarkorDeleteNote",
        "version": "projected_v6_schema",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v6_schema/episodes/selective_20260915/"
            "projected_v6_schema/MarkorDeleteNote/b01/reactive/trajectory.jsonl"
        ),
    },
    {
        "family": "MarkorDeleteNote",
        "version": "projected_v6_schema",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v6_schema/episodes/selective_20260915/"
            "projected_v6_schema/MarkorDeleteNote/b02/reactive/trajectory.jsonl"
        ),
    },
)

# These are a diagnostic reproduction target, not a validity criterion and
# never a reason to alter the construction algorithm.
PRIOR_OBSERVED_REFERENCE_LENGTHS = {
    "MarkorCreateNote": 4,
    "FilesMoveFile": 12,
    "MarkorDeleteNote": 4,
}

_VISIBLE_FIELDS = (
    ("text", "text"),
    ("content_description", "description"),
    ("description", "description"),
    ("hint", "hint"),
    ("hint_text", "hint"),
)
_SOURCE_PATHS = {
    "control": Path(__file__).resolve(),
    "ax_parser": Path(selective_ax_parser.__file__).resolve(),
    "evidence_store": Path(selective_evidence_store.__file__).resolve(),
    "guard_evidence": Path(selective_guard_evidence.__file__).resolve(),
    "plan_contract": Path(selective_plan_contract.__file__).resolve(),
    "prefix_contract": Path(selective_prefix.__file__).resolve(),
    "runtime": Path(selective_runtime.__file__).resolve(),
}


class ControlStop(RuntimeError):
    """Fail-closed offline control error."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ControlStop(f"required public artifact is unreadable: {path}") from exc


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError as exc:
        raise ControlStop(f"artifact is outside the frozen workspace: {path}") from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")


def _exclusive_output(path: Path) -> None:
    if path.exists():
        raise ControlStop(
            f"offline control output already exists; refusing overwrite: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError as exc:
        raise ControlStop(
            f"offline control output was created concurrently: {path}"
        ) from exc


def source_manifest() -> dict[str, Any]:
    hashes: dict[str, Any] = {}
    for name, path in _SOURCE_PATHS.items():
        if not path.is_file():
            raise ControlStop(f"required validator source is missing: {name}")
        hashes[name] = {"path": _relative(path), "sha256": _sha256_file(path)}
    return {
        "source_sha256": hashes,
        "python": sys.version,
        "platform": platform.platform(),
        "working_directory": "computer-use",
        "interpreter": "../.venv-android/bin/python",
    }


def _read_public_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ControlStop(f"public trajectory is unreadable: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ControlStop(
                f"public trajectory JSON is invalid at line {line_number}: {path}"
            ) from exc
        if not isinstance(value, Mapping):
            raise ControlStop(f"public trajectory row is not an object: {path}")
        # Final records are deliberately discarded before any field is read;
        # their status/success fields are not an input to this control.
        if value.get("record_type") in {"initial", "model_call", "step"}:
            rows.append(dict(value))
    return rows


def _goal_from_rows(rows: Sequence[Mapping[str, Any]], path: Path) -> str:
    for row in rows:
        value = row.get("goal_text")
        if (
            row.get("record_type") == "initial"
            and isinstance(value, str)
            and value.strip()
        ):
            return value
    for row in rows:
        if row.get("record_type") != "model_call":
            continue
        prompt = row.get("prompt")
        contents: list[str] = []
        if isinstance(prompt, str):
            contents.append(prompt)
        elif isinstance(prompt, Sequence) and not isinstance(
            prompt, (str, bytes, bytearray)
        ):
            contents.extend(
                str(item.get("content"))
                for item in prompt
                if isinstance(item, Mapping) and isinstance(item.get("content"), str)
            )
        for content in contents:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            for index, line in enumerate(lines[:-1]):
                if line == "You are operating an Android phone.":
                    return lines[index + 1]
            match = re.search(r'"task_goal_text"\s*:\s*("(?:\\.|[^"\\])*")', content)
            if match:
                try:
                    value = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, str) and value.strip():
                    return value
    raise ControlStop(f"public trajectory has no recorded goal text: {path}")


def load_public_demo(
    path: Path | str, family: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only goal and pre/post/action fields from one public trace."""

    path = Path(path).resolve()
    if not path.is_file():
        raise ControlStop(f"public trajectory is missing: {path}")
    rows = _read_public_rows(path)
    steps: list[dict[str, Any]] = []
    for row in rows:
        if row.get("record_type") != "step":
            continue
        action = row.get("action")
        pre = row.get("pre_obs")
        post = row.get("post_obs")
        if (
            not isinstance(action, Mapping)
            or not isinstance(pre, Mapping)
            or not isinstance(post, Mapping)
        ):
            raise ControlStop(f"public step lacks action/pre/post evidence: {path}")
        steps.append(
            {
                "step": row.get("step"),
                "action": copy.deepcopy(dict(action)),
                "pre_obs": copy.deepcopy(dict(pre)),
                "post_obs": copy.deepcopy(dict(post)),
            }
        )
    if not steps:
        raise ControlStop(f"public trajectory has no complete steps: {path}")
    goal_text = _goal_from_rows(rows, path)
    demo = {"family": family, "goal_text": goal_text, "steps": steps}
    metadata = {
        "family": family,
        "path": _relative(path),
        "absolute_path": str(path),
        "sha256": _sha256_file(path),
        "goal_text_sha256": _sha256_bytes(goal_text.encode("utf-8")),
        "action_count": len(steps),
        "public_fields_used": ["goal_text", "action", "pre_obs", "post_obs"],
        "oracle_fields_used": [],
    }
    return demo, metadata


def _nodes(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    parsed = selective_ax_parser.parse_ax_tree(
        str(observation.get("ax_tree_text") or "")
    )
    return [
        dict(node) for node in (parsed.get("nodes") or []) if isinstance(node, Mapping)
    ]


def _visible_selectors(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    selectors: list[dict[str, Any]] = []
    for source, target in _VISIBLE_FIELDS:
        value = node.get(source)
        if isinstance(value, str) and value.strip():
            selectors.append({target: value})
    return selectors


def unique_observed_selector(
    nodes: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the first unique visible selector, then the first unique flags."""

    detached = [dict(node) for node in nodes]
    for node in detached:
        for selector in _visible_selectors(node):
            hits, error = selective_runtime._hits(selector, detached, {})
            if error is None and len(hits) == 1:
                return selector
    # This fallback is part of the original diagnostic transform.  It is
    # still required to be unique against the complete observed pre-state.
    for node in detached:
        fields: dict[str, Any] = {}
        if isinstance(node.get("is_editable"), bool):
            fields["editable"] = node["is_editable"]
        if isinstance(node.get("is_clickable"), bool):
            fields["clickable"] = node["is_clickable"]
        if fields:
            hits, error = selective_runtime._hits(fields, detached, {})
            if error is None and len(hits) == 1:
                return fields
    return None


def _selector_for_effect(
    effect: Mapping[str, Any], post_nodes: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    field_names = {source: target for source, target in _VISIBLE_FIELDS}
    for item in effect.get("post_only_visible_values") or []:
        if not isinstance(item, Mapping):
            continue
        field = field_names.get(item.get("field"))
        value = item.get("value")
        if field is None or not isinstance(value, str) or not value.strip():
            continue
        selector = {field: value}
        hits, error = selective_runtime._hits(selector, list(post_nodes), {})
        if error is None and len(hits) == 1:
            return selector
    return None


def build_literal_prefix(demo: Mapping[str, Any]) -> dict[str, Any]:
    """Build the exact literal diagnostic prefix from one public demo."""

    family = str(demo.get("family") or "")
    steps = list(demo.get("steps") or [])
    try:
        store = selective_evidence_store.build_store(
            str(demo.get("goal_text") or ""), steps
        )
    except (selective_evidence_store.EvidenceStoreError, TypeError, ValueError) as exc:
        raise ControlStop(
            f"training evidence store failed for {family}: {type(exc).__name__}"
        ) from exc

    plan_steps: list[dict[str, Any]] = []
    stop: dict[str, Any] | None = None
    for source_step, step in enumerate(steps, start=1):
        action = step.get("action")
        if not isinstance(action, Mapping):
            stop = {"source_step": source_step, "reason": "action_missing"}
            break
        action_without_index = {
            key: copy.deepcopy(value) for key, value in action.items() if key != "index"
        }
        pre_nodes = _nodes(step["pre_obs"])
        post_nodes = _nodes(step["post_obs"])
        target: dict[str, Any] | None = None
        if isinstance(action.get("index"), int):
            target_record = store.query(source_step, "target")
            target_node = (
                target_record.get("target_node")
                if target_record.get("status") == "found"
                else None
            )
            target = unique_observed_selector(
                [target_node] if isinstance(target_node, Mapping) else []
            )
            if (
                target is None
                or len(selective_runtime._hits(target, pre_nodes, {})[0]) != 1
            ):
                stop = {
                    "source_step": source_step,
                    "reason": "target_descriptor_unavailable_or_nonunique",
                }
                break

        before: list[dict[str, Any]] = []
        if target is None and action.get("action_type") != "open_app":
            before_selector = unique_observed_selector(pre_nodes)
            if before_selector is None:
                stop = {
                    "source_step": source_step,
                    "reason": "before_witness_unavailable",
                }
                break
            before = [before_selector]

        after_selector = _selector_for_effect(
            store.query(source_step, "effect"), post_nodes
        )
        if after_selector is None:
            stop = {
                "source_step": source_step,
                "reason": "after_witness_unavailable_or_nonunique",
            }
            break
        plan_steps.append(
            {
                "id": f"trace_{source_step}",
                "intent": f"replay source step {source_step}",
                "action": action_without_index,
                "target": target,
                "before": before,
                "after": [after_selector],
            }
        )

    if not plan_steps:
        return {
            "status": "abstained",
            "valid": False,
            "family": family,
            "prefix_length": 0,
            "stop": stop,
            "candidate": None,
            "validator": None,
        }

    plan = {"schema": "selective-plan/1", "slots": {}, "steps": plan_steps}
    candidate = {
        "schema": "selective-prefix/1",
        "plan": plan,
        "source_steps": {
            step["id"]: index for index, step in enumerate(plan_steps, start=1)
        },
        "terminal_source_step": len(plan_steps),
        "handoff_policy": "reactive",
    }
    validator = selective_prefix.validate_prefix_response(candidate, [dict(demo)])
    valid = validator.get("valid") is True
    return {
        "status": "valid" if valid else "validator_rejected",
        "valid": valid,
        "family": family,
        "prefix_length": len(plan_steps),
        "stop": stop,
        "candidate": candidate,
        "validator": dict(validator),
    }


def _mismatches(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    guard = report.get("guard_evidence")
    if isinstance(guard, Mapping):
        for item in guard.get("steps") or []:
            if not isinstance(item, Mapping) or item.get("status") == "valid":
                continue
            mismatches.append(
                {
                    "step_id": item.get("step_id"),
                    "source_indices": list(item.get("source_indices") or []),
                    "errors": [str(error) for error in (item.get("errors") or [])[:8]],
                }
            )
    if not mismatches:
        mismatches.extend(
            {"error": str(error)} for error in (report.get("errors") or [])[:8]
        )
    return mismatches


def compare_literal_to_heldout(
    training_result: Mapping[str, Any],
    heldout_demo: Mapping[str, Any],
    heldout_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = training_result.get("candidate")
    if not isinstance(candidate, Mapping):
        return {
            **dict(heldout_metadata),
            "comparison_type": "literal_training_candidate_vs_public_heldout_steps",
            "plan_prefix_length": training_result.get("prefix_length"),
            "valid": False,
            "mismatches": [{"error": "training candidate was unavailable"}],
            "task_success_evaluated": False,
        }
    report = selective_prefix.validate_prefix_response(candidate, [dict(heldout_demo)])
    return {
        **dict(heldout_metadata),
        "comparison_type": "literal_training_candidate_vs_public_heldout_steps",
        "plan_prefix_length": training_result.get("prefix_length"),
        "valid": report.get("valid") is True,
        "mismatches": _mismatches(report),
        "task_success_evaluated": False,
    }


def _trace_path(origin: Path, relative_path: str) -> Path:
    path = (
        (origin / Path(relative_path).relative_to(REPO_ROOT.name)).resolve()
        if relative_path.startswith(REPO_ROOT.name + "/")
        else (REPO_ROOT / relative_path).resolve()
    )
    # All frozen paths are workspace-relative constants.  Reject accidental
    # traversal before opening anything.
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ControlStop(
            f"frozen trace path is outside the workspace: {relative_path}"
        ) from exc
    return path


def run_control(
    origin: Path | str = DEFAULT_ORIGIN,
    out: Path | str = DEFAULT_OUT,
) -> dict[str, Any]:
    """Reproduce the offline diagnostic and write one exclusive artifact set."""

    origin = Path(origin).resolve()
    out = Path(out).resolve()
    if not origin.is_dir():
        raise ControlStop(f"selective origin directory is missing: {origin}")
    training_manifest = origin / "training_manifest.json"
    if not training_manifest.is_file():
        raise ControlStop("public training manifest is missing")
    training_manifest_hash = _sha256_file(training_manifest)

    training: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for item in TRAINING_TRACES:
        path = _trace_path(origin, item["relative_path"])
        demo, metadata = load_public_demo(path, item["family"])
        training[item["family"]] = (demo, metadata)

    training_results: list[dict[str, Any]] = []
    for item in TRAINING_TRACES:
        demo, metadata = training[item["family"]]
        built = build_literal_prefix(demo)
        training_results.append(
            {
                **metadata,
                "prefix_length": built["prefix_length"],
                "status": built["status"],
                "valid": built["valid"],
                "stop": built["stop"],
                "candidate": built["candidate"],
                "validator": built["validator"],
                "prior_observed_reference_length": PRIOR_OBSERVED_REFERENCE_LENGTHS.get(
                    item["family"]
                ),
                "reproduction_match": built["prefix_length"]
                == PRIOR_OBSERVED_REFERENCE_LENGTHS.get(item["family"]),
            }
        )

    heldout_results: list[dict[str, Any]] = []
    for item in HELDOUT_TRACES:
        path = _trace_path(origin, item["relative_path"])
        demo, metadata = load_public_demo(path, item["family"])
        training_result = next(
            result for result in training_results if result["family"] == item["family"]
        )
        heldout = compare_literal_to_heldout(training_result, demo, metadata)
        heldout["version"] = item["version"]
        heldout_results.append(heldout)

    sources = source_manifest()
    config: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "namespace": NAMESPACE,
        "origin": str(origin),
        "origin_relative": _relative(origin),
        "training_manifest": {
            "path": _relative(training_manifest),
            "sha256": training_manifest_hash,
            "used_for": "provenance hash only; manifest oracle/status fields ignored",
        },
        "algorithm": {
            "name": "unique_observed_selector_effect_specific_post_only_guard",
            "target": (
                "first unique visible selector, then first unique "
                "editable/clickable flag fallback"
            ),
            "before": (
                "first unique observed selector for targetless "
                "non-open_app actions"
            ),
            "after": (
                "first post-only visible value whose exact selector is "
                "unique in post state"
            ),
            "action_literals": (
                "retain every recorded action field literally and omit "
                "observation-local index"
            ),
            "slots": "none",
            "stop": "first unavailable, ambiguous, nonunique, or missing witness",
            "model_calls": 0,
            "ui_actions": 0,
            "oracle_inputs": False,
            "private_inputs": False,
        },
        "training_trace_count": len(training_results),
        "heldout_trace_count": len(heldout_results),
        "source_manifest": sources,
        "source_manifest_sha256": _sha256_bytes(_canonical(sources).encode("utf-8")),
        "public_input_paths": [
            metadata["path"]
            for metadata in [result for result in training_results] + heldout_results
        ],
        "results_file": "results.json",
        "config_file": "config.json",
        "source_manifest_file": "source_manifest.json",
    }
    results: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "complete",
        "training": training_results,
        "heldout_literal_comparisons": heldout_results,
        "diagnostic_only": {
            "prior_observed_reference_lengths": PRIOR_OBSERVED_REFERENCE_LENGTHS,
            "reproduction_matches": all(
                item["reproduction_match"] for item in training_results
            ),
            "metric_is_not_heldout_generalization": True,
        },
        "task_success_evaluated": False,
        "model_calls": 0,
        "ui_actions": 0,
        "oracle_inputs": [],
        "private_inputs": [],
    }
    result_payload = (
        json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    config["results_sha256"] = _sha256_bytes(result_payload.encode("utf-8"))
    _exclusive_output(out)
    _write_json(out / "source_manifest.json", sources)
    (out / "results.json").write_text(result_payload, encoding="utf-8")
    _write_json(out / "config.json", config)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", action="store_true", help="run the bounded offline control"
    )
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
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(Path(args.out).resolve()),
                "training_prefix_lengths": {
                    item["family"]: item["prefix_length"] for item in result["training"]
                },
                "heldout_comparisons": len(result["heldout_literal_comparisons"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI smoke.
    raise SystemExit(main())


__all__ = [
    "DEFAULT_ORIGIN",
    "DEFAULT_OUT",
    "HELDOUT_TRACES",
    "PRIOR_OBSERVED_REFERENCE_LENGTHS",
    "SCHEMA",
    "TRAINING_TRACES",
    "VERSION",
    "ControlStop",
    "build_literal_prefix",
    "compare_literal_to_heldout",
    "load_public_demo",
    "main",
    "run_control",
    "source_manifest",
    "unique_observed_selector",
]

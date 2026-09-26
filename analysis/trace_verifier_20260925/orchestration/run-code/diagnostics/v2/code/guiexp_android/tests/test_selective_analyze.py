"""Offline accounting tests for the selective analysis report."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from guiexp_android.selective_analyze import analyze

FAMILY = "FixtureFamily"
ARMS = ("reactive", "full_fallback", "local_rejoin")
NAMESPACE = "selective_20260915"


def _episode(family: str, binding: str, arm: str) -> str:
    return f"{NAMESPACE}/{family}/{binding}/{arm}"


def _receipt_id(episode: str, sequence: int) -> str:
    return f"{NAMESPACE}/v9/{episode.split('/', 1)[1]}/execution-fixture/logical-{sequence:04d}/attempt-1"


def _write_state(out: Path, episode: str, status: str, result: dict | None = None) -> None:
    path = out / "episodes" / episode / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"record_type": "episode-state", "status": status, "episode_id": episode}
    if result is not None:
        state["result"] = result
    path.write_text(json.dumps(state), encoding="utf-8")


def _write_calls(out: Path) -> Path:
    ledger = out.parent / "revision_20260913" / "budget.sqlite3"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(ledger)
    database.execute(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, episode TEXT NOT NULL, model TEXT NOT NULL, "
        "request_sha TEXT NOT NULL, reserved_nano INTEGER NOT NULL, actual_nano INTEGER, "
        "state TEXT NOT NULL, response_json TEXT, error_type TEXT, created REAL NOT NULL)"
    )

    def add(episode: str, sequence: int, state: str, reserved: int, actual: int | None, usage: dict | None, error: str | None, created: float) -> None:
        response = json.dumps({"usage": usage}) if usage is not None else None
        database.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_receipt_id(episode, sequence), episode, "fixture-model", f"sha-{created}", reserved, actual, state, response, error, created),
        )

    train_a = f"{NAMESPACE}/train/{FAMILY}/s1/a0"
    train_b = f"{NAMESPACE}/train/{FAMILY}/s2/a0"
    build = f"{NAMESPACE}/build/{FAMILY}"
    add(train_a, 1, "settled", 200, 100, {"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 1}}, None, 1)
    add(train_b, 1, "uncertain", 300, None, {}, "RateLimitError", 2)
    add(build, 1, "settled", 50, 50, {"prompt_tokens": 5, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 0}}, None, 3)

    b01_reactive = _episode(FAMILY, "b01", "reactive")
    b01_full = _episode(FAMILY, "b01", "full_fallback")
    b01_local = _episode(FAMILY, "b01", "local_rejoin")
    b02_reactive = _episode(FAMILY, "b02", "reactive")
    b02_full = _episode(FAMILY, "b02", "full_fallback")
    add(b01_reactive, 1, "settled", 20, 10, {"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 1}}, None, 4)
    add(b01_full, 1, "settled", 30, 20, {"prompt_tokens": 11, "completion_tokens": 3}, None, 5)
    add(b01_local, 1, "uncertain", 40, None, {}, "TransportUnknown", 6)
    add(b02_reactive, 1, "reserved", 50, None, None, None, 7)
    add(b02_full, 1, "overrun", 8, 7, {"prompt_tokens": 2}, "Overrun", 8)
    # This old-revision row proves that the whole-budget total is reported
    # separately from the selective namespace phase totals.
    database.execute(
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("v9/old/logical-0001", "old_revision/episode", "old-model", "old-sha", 999, 999, "settled", None, None, 9),
    )
    database.commit()
    database.close()
    return ledger


def _write_local_records(out: Path) -> None:
    records = {
        f"{NAMESPACE}/train/{FAMILY}/s1/a0": [
            {"record_type": "model_call", "episode_id": f"{NAMESPACE}/train/{FAMILY}/s1/a0", "call_index": 1, "purpose": "training_discover", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 1}},
        ],
        _episode(FAMILY, "b01", "reactive"): [
            {"record_type": "model_call", "episode_id": _episode(FAMILY, "b01", "reactive"), "call_index": 1, "purpose": "reactive_resume", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 1}},
        ],
        _episode(FAMILY, "b01", "full_fallback"): [
            {"record_type": "model_call", "episode_id": _episode(FAMILY, "b01", "full_fallback"), "call_index": 1, "purpose": "binding_extraction", "usage": {"prompt_tokens": 11, "completion_tokens": 3, "cached_tokens": None}},
        ],
        _episode(FAMILY, "b01", "local_rejoin"): [
            {"record_type": "model_call", "episode_id": _episode(FAMILY, "b01", "local_rejoin"), "call_index": 1, "purpose": "binding_extraction", "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cached_tokens": 0}},
            {"record_type": "model_call", "episode_id": _episode(FAMILY, "b01", "local_rejoin"), "call_index": 2, "purpose": "local_rejoin", "usage": {"prompt_tokens": 13, "completion_tokens": 5, "cached_tokens": 2}},
        ],
    }
    for episode, values in records.items():
        path = out / ("training" if "/train/" in episode else "episodes") / episode / "trajectory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(item) for item in values) + "\n", encoding="utf-8")
    build_path = out / "build" / FAMILY / "first_response.json"
    build_path.parent.mkdir(parents=True, exist_ok=True)
    build_path.write_text(json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 1}}), encoding="utf-8")


def _write_training_build_states(out: Path) -> None:
    for attempt, status, episode in (
        ("s1_a0", "budget_stopped", f"{NAMESPACE}/train/{FAMILY}/s1/a0"),
        ("s2_a0", "interrupted", f"{NAMESPACE}/train/{FAMILY}/s2/a0"),
    ):
        path = out / "training" / FAMILY / attempt / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"record_type": "training-state", "status": status, "episode_id": episode}), encoding="utf-8")
    path = out / "build" / FAMILY / "build.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"record_type": "selective-build", "status": "interrupted"}), encoding="utf-8")


def _spec(out: Path, ledger: Path) -> dict:
    episodes = []
    for binding in ("b01", "b02"):
        for arm in ARMS:
            episodes.append({
                "id": _episode(FAMILY, binding, arm),
                "family": FAMILY,
                "binding_id": binding,
                "arm": arm,
                "seed": 1,
            })
    return {
        "schema": "selective-pilot/1",
        "namespace": NAMESPACE,
        "model": "fixture-model",
        "families": [FAMILY],
        "arms": list(ARMS),
        "episodes": episodes,
        "ledger_absolute": str(ledger),
        "spec_sha256": "verified-fixture",
        "source_manifest_sha256": "source-fixture",
        "budget_manifest_sha256": "budget-fixture",
    }


def _version_spec(ledger: Path, origin: Path | None = None) -> dict:
    base = _episode(FAMILY, "b01", "reactive")
    rows = []
    for arm in ARMS:
        nested = f"{NAMESPACE}/compiler_v2/{FAMILY}/b01/{arm}"
        row = {"id": nested, "family": FAMILY, "binding_id": "b01", "arm": arm, "seed": 1}
        if arm == "reactive":
            row.update({"source_episode_id": base, "receipt_episode_id": base})
        else:
            row.update({
                "source_episode_id": _episode(FAMILY, "b01", arm),
                "receipt_episode_id": nested,
            })
        rows.append(row)
    return {
        "schema": "selective-pilot/1",
        "namespace": NAMESPACE,
        "version": "compiler_v2",
        "model": "fixture-model",
        "families": [FAMILY],
        "arms": list(ARMS),
        "episodes": rows,
        "ledger_absolute": str(ledger),
        "spec_sha256": "v2-fixture",
        "source_manifest_sha256": "v2-source",
        "budget_manifest_sha256": "v2-budget",
        "provenance": {
            "origin_out": str(origin or ledger.parent.parent / "selective_20260915"),
            "origin_spec_sha256": "base-spec-sha",
            "source_hashes": {"selective_pilot.py": "base-source-sha"},
        },
    }


def test_analyze_conserves_fees_and_reuses_shared_build(tmp_path):
    out = tmp_path / "selective_20260915"
    out.mkdir()
    ledger = _write_calls(out)
    _write_local_records(out)
    _write_training_build_states(out)
    b01_reactive = _episode(FAMILY, "b01", "reactive")
    b01_full = _episode(FAMILY, "b01", "full_fallback")
    b01_local = _episode(FAMILY, "b01", "local_rejoin")
    _write_state(out, b01_reactive, "done", {"success": True, "actions": 2, "run": {}})
    _write_state(out, b01_full, "done", {"success": False, "actions": 2, "run": {"plan": {"trace": [{"kind": "program", "guards": {"after": {"ok": False}}}]}}})
    _write_state(
        out,
        b01_local,
        "done",
        {
            "success": True,
            "actions": 4,
            "run": {"plan": {"trace": [
                {"kind": "program", "guards": {"after": {"ok": False}}},
                {"kind": "local", "issued": True, "guards": {"after": {"ok": False}}},
                {"kind": "local", "issued": True, "guards": {"after": {"ok": True}}},
                {"kind": "program", "issued": True, "guards": {"after": {"ok": True}}},
            ]}},
        },
    )
    _write_state(out, _episode(FAMILY, "b02", "reactive"), "budget_stopped")
    _write_state(out, _episode(FAMILY, "b02", "full_fallback"), "interrupted")

    before = sqlite3.connect(ledger).execute("SELECT id,state,actual_nano,reserved_nano FROM calls ORDER BY created").fetchall()
    report = analyze(out, _spec(out, ledger))
    after = sqlite3.connect(ledger).execute("SELECT id,state,actual_nano,reserved_nano FROM calls ORDER BY created").fetchall()

    assert before == after
    assert (out / "analysis.json").is_file()
    assert (out / "analysis.md").is_file()
    assert report["ledger"]["mode"] == "ro"
    assert len(report["receipts"]) == 8
    assert len(report["serving_episodes"]) == 6
    assert report["source_freeze"]["analyzer_version"] == "selective-analysis/1"
    assert len(report["source_freeze"]["analyzer_sha256"]) == 64

    train = report["phase_costs"]["train"]
    assert (train["settled_count"], train["unknown_bill_count"], train["lower_bound_usd"], train["upper_bound_usd"]) == (1, 1, "0.0000001", "0.0000004")
    serving = report["phase_costs"]["serving"]
    assert (serving["in_flight_count"], serving["unknown_bill_count"], serving["unknown_bill_total_count"], serving["incomplete_count"]) == (1, 1, 2, 1)
    assert (serving["lower_bound_usd"], serving["upper_bound_usd"]) == ("0.000000037", "0.000000135")
    assert report["ledger"]["whole_budget"]["row_count"] == 9
    assert (report["ledger"]["whole_budget"]["in_flight_count"], report["ledger"]["whole_budget"]["unknown_bill_count"], report["ledger"]["whole_budget"]["unknown_bill_total_count"]) == (1, 2, 3)
    assert report["ledger"]["whole_budget"]["lower_bound_usd"] == "0.000001186"
    assert report["ledger"]["whole_budget"]["upper_bound_usd"] == "0.000001584"
    assert report["terminal_states"]["train"]["status_counts"] == {"budget_stopped": 1, "interrupted": 1}
    assert sorted(record["owned_receipt_count"] for record in report["terminal_states"]["train"]["records"]) == [1, 1]
    assert report["terminal_states"]["build"]["status_counts"] == {"interrupted": 1}

    local = next(item for item in report["serving_episodes"] if item["arm"] == "local_rejoin" and item["binding_id"] == "b01")
    assert (local["verified_rejoin_events"], local["local_rejoin_actions"], local["program_actions_following_join"], local["useful_local_rejoin"]) == (1, 2, 1, True)
    assert local["model_calls"] == 2
    assert local["model_call_tokens"]["prompt_tokens"] == 25
    assert local["model_call_tokens"]["cached_tokens"] == 2
    assert local["model_call_tokens"]["missing"]["total_tokens"] == 2
    missing = next(item for item in report["serving_episodes"] if item["arm"] == "local_rejoin" and item["binding_id"] == "b02")
    assert missing["state_status"] == "missing"

    comparison = report["serving_comparisons"][FAMILY]
    assert comparison["eligible_complete_matched_bindings"] == 1
    assert comparison["arms"]["full_fallback"]["failed_complete_count"] == 1
    assert comparison["arms"]["full_fallback"]["exact_bill_mean_usd"] == "0.00000002"
    assert comparison["arms"]["local_rejoin"]["exact_bill_episode_count"] == 0
    assert comparison["arms"]["local_rejoin"]["unknown_bill_episode_count"] == 1
    interrupted = next(item for item in comparison["ineligible_bindings"] if item["binding_id"] == "b02")
    assert interrupted["arm_status"]["full_fallback"] == "interrupted"
    assert interrupted["arm_fees"]["full_fallback"]["incomplete_count"] == 1

    economics = report["counterfactual_economics"][FAMILY]
    assert economics["reactive"]["lower_bound_usd"] == "0.00000011"
    assert economics["reactive"]["upper_bound_usd"] == "0.00000046"
    assert economics["full_fallback"]["lower_bound_usd"] == "0.000000177"
    assert economics["full_fallback"]["upper_bound_usd"] == "0.000000485"
    assert economics["local_rejoin"]["lower_bound_usd"] == "0.00000015"
    assert economics["local_rejoin"]["upper_bound_usd"] == "0.00000049"
    assert economics["full_fallback"]["physical_build_receipts_counted_once"] == 1
    assert economics["local_rejoin"]["physical_build_receipts_counted_once"] == 1
    assert report["phase_costs"]["build"]["receipt_count"] == 1


def test_purpose_costs_keep_ambiguous_receipts_unattributed(tmp_path):
    out = tmp_path / "selective_20260915"
    out.mkdir()
    ledger = _write_calls(out)
    _write_local_records(out)
    report = analyze(out, _spec(out, ledger))
    purposes = report["purpose_costs"]["serving"]
    assert purposes["binding_extraction"]["receipt_count"] == 2
    assert purposes.get("local_rejoin", {"receipt_count": 0})["receipt_count"] == 0
    assert purposes["unattributed"]["receipt_count"] == 2
    assert report["accounting"]["unknown_costs_are_not_zero"] is True


def test_build_repair_episode_is_attributed_without_reusing_sequence(tmp_path):
    out = tmp_path / "selective_20260915"
    out.mkdir()
    ledger = _write_calls(out)
    repair_episode = f"{NAMESPACE}/build/{FAMILY}/repair"
    database = sqlite3.connect(ledger)
    database.execute(
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_receipt_id(repair_episode, 1), repair_episode, "fixture-model", "repair-sha", 9, 9, "settled", json.dumps({"usage": {"prompt_tokens": 3}}), None, 10),
    )
    database.commit()
    database.close()
    (out / "build" / FAMILY).mkdir(parents=True)
    (out / "build" / FAMILY / "repair_response.json").write_text(json.dumps({"usage": {"prompt_tokens": 3}}), encoding="utf-8")
    report = analyze(out, _spec(out, ledger))
    assert report["purpose_costs"]["build"]["build_repair"]["receipt_count"] == 1


def test_compiler_v2_imports_current_baseline_and_separates_sunk_build(tmp_path):
    origin = tmp_path / "selective_20260915"
    origin.mkdir()
    out = tmp_path / "versions" / "compiler_v2"
    out.mkdir(parents=True)
    ledger = _write_calls(origin)
    _write_local_records(origin)
    _write_training_build_states(origin)
    base_reactive = _episode(FAMILY, "b01", "reactive")
    _write_state(origin, base_reactive, "done", {"success": True, "actions": 2, "run": {}})

    version_build = f"{NAMESPACE}/compiler_v2/build/{FAMILY}"
    version_full = f"{NAMESPACE}/compiler_v2/{FAMILY}/b01/full_fallback"
    version_local = f"{NAMESPACE}/compiler_v2/{FAMILY}/b01/local_rejoin"
    database = sqlite3.connect(ledger)
    for episode, actual, created in ((version_build, 30, 11), (version_full, 40, 12), (version_local, 50, 13)):
        database.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_receipt_id(episode, 1), episode, "fixture-model", f"v2-{created}", 60, actual, "settled", json.dumps({"usage": {"prompt_tokens": 3}}), None, created),
        )
    database.commit()
    database.close()
    build_dir = out / "build" / FAMILY
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "build.json").write_text(json.dumps({"status": "built"}), encoding="utf-8")
    (build_dir / "first_response.json").write_text(json.dumps({"usage": {"prompt_tokens": 3}}), encoding="utf-8")
    _write_state(out, version_full, "done", {"success": False, "actions": 2, "run": {"plan": {"trace": []}}})
    _write_state(out, version_local, "done", {"success": True, "actions": 3, "run": {"plan": {"trace": []}}})

    report = analyze(out, _version_spec(ledger, origin))
    assert report["version"] == "compiler_v2"
    assert report["provenance"]["origin_spec_sha256"] == "base-spec-sha"
    assert len(report["imported_rows"]) == 0
    assert len(report["versioned_rows"]) == 3
    assert len(report["receipts"]) == 7
    assert len({item["id"] for item in report["receipts"]}) == 7
    assert report["phase_costs"]["build"]["receipt_count"] == 2
    assert report["version_phase_costs"]["build"]["receipt_count"] == 1
    assert report["development_build_costs"]["physical_receipt_count"] == 1
    assert report["version_build_costs"]["physical_receipt_count"] == 1
    assert report["version_build_costs"]["families"][FAMILY]["status"] == "built"
    assert report["terminal_states"]["development_build"]["status_counts"] == {"interrupted": 1}
    version_full_entry = next(item for item in report["serving_episodes"] if item["arm"] == "full_fallback")
    assert version_full_entry["complete"] is True
    assert version_full_entry["final_success"] is False
    assert version_full_entry["source_episode_id"] == _episode(FAMILY, "b01", "full_fallback")

    comparison = report["serving_comparisons"][FAMILY]
    assert comparison["eligible_complete_matched_bindings"] == 1
    assert comparison["arms"]["full_fallback"]["failed_complete_count"] == 1
    economics = report["counterfactual_economics"][FAMILY]
    assert economics["full_fallback"]["version_lower_bound_usd"] == "0.00000017"
    assert economics["full_fallback"]["cumulative_including_all_attempts_lower_bound_usd"] == "0.00000022"
    assert economics["local_rejoin"]["version_lower_bound_usd"] == "0.00000018"
    assert economics["local_rejoin"]["cumulative_including_all_attempts_upper_bound_usd"] == "0.00000053"

"""Arithmetic of the constants-table extractor, on a synthetic cell."""

from __future__ import annotations

import json

import pytest

from guiexp_android import constants_table as ct


MODEL = "z-ai/glm-5.3-flash"
R = ct.CACHE_RATIO[MODEL]
R_C = ct.PRICE_SHEET[MODEL]["p_c"] / ct.PRICE_SHEET[MODEL]["p_in"]
R_O = ct.PRICE_SHEET[MODEL]["p_o"] / ct.PRICE_SHEET[MODEL]["p_in"]
FLOOR = ct.FLOOR_RAW_TOKENS[MODEL]
P_IN = ct.PRICE_SHEET[MODEL]["p_in"]


def pw(prompt: int, cached: int, completion: int) -> float:
    """The headline unit: price-weighted tokens."""
    return (prompt - cached) + R_C * cached + R_O * completion


def usd(bill_tokens: float) -> float:
    """A bill worth this many tokens in the secondary usd_over_p_in unit."""
    return bill_tokens * P_IN


def usage(prompt: int, completion: int, bill_tokens: float) -> dict:
    return {
        "prompt_tokens": prompt,
        "cached_tokens": 0,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": usd(bill_tokens),
    }


def write_cell(root, slug, family, *, gate_after, unautomatable, deploy,
               no_total_cost=False, gate_k3=5, redeploy=None):
    cell = root / slug / family
    (cell / "explore").mkdir(parents=True)
    (cell / "doc_arm" / "s4").mkdir(parents=True)

    episodes = [
        {"seed": 1, "attempt": 0, "reused": False, "success": True, "steps": 2,
         **usage(10000, 1000, 10_000), "model_calls": 2},
        {"seed": 2, "attempt": 0, "reused": False, "success": True, "steps": 2,
         **usage(20000, 2000, 20_000), "model_calls": 2},
        {"seed": 3, "attempt": 0, "reused": False, "success": False, "steps": 2,
         **usage(30000, 3000, 30_000), "model_calls": 2},
        {"seed": 3, "attempt": 1, "reused": False, "success": False, "steps": 2,
         **usage(40000, 4000, 40_000), "model_calls": 2},
    ]
    build = {
        "family": family,
        "model": MODEL,
        "cache_ratio_r": R,
        "record_type": "build",
        "stages_done": ["exploration", "translator", "builder", "gate_per_k",
                        "verification", "deploy", "doc_arm"],
        "exploration": {
            "per_episode": episodes,
            # One reflection call before the single retry episode above.
            "reflections": [{"seed": 3, "for_attempt": 1, "prompt_tokens": 4000,
                             "cached_tokens": 0, "completion_tokens": 1000,
                             "cost_usd": usd(20_000)}],
            "retry_episodes": 1,
            "totals": {"calls": 9, "prompt_tokens": 104000, "cached_tokens": 20000,
                       "completion_tokens": 11000, "total_tokens": 115000,
                       "cost_usd": usd(120_000)},
        },
        "translator": {
            "per_trajectory": [],
            "totals": {"calls": 2, "prompt_tokens": 400, "cached_tokens": 0,
                       "completion_tokens": 100, "total_tokens": 500, "cost_usd": usd(5_000)},
        },
        "builder": {
            "initial": {
                "k3_code": {"k": 3, "artifact": "code", "prompt_tokens": 600,
                            "completion_tokens": 400, "cached_tokens": 0, "cost_usd": usd(10_000)},
                "k3_doc": {"k": 3, "artifact": "doc", "prompt_tokens": 100,
                           "completion_tokens": 100, "cached_tokens": 0, "cost_usd": usd(2_000)},
            },
            "totals_all_calls": {"calls": 2, "prompt_tokens": 700, "cached_tokens": 0,
                                 "completion_tokens": 500, "total_tokens": 1200, "cost_usd": usd(12_000)},
            "selected_arm": {
                "k": 3, "artifact": "code",
                "initial_plus_refinements": {"calls": 2, "prompt_tokens": 900,
                                             "cached_tokens": 0, "completion_tokens": 600,
                                             "total_tokens": 1500, "cost_usd": usd(15_000)},
            },
        },
        "gate_per_k": {
            "k1": {"bindings_passed": 0, "bindings_total": 5},
            "k2": {"bindings_passed": 2, "bindings_total": 5},
            "k3": {"bindings_passed": gate_k3, "bindings_total": 5},
        },
        "verification": {
            "selected_k": 3,
            "refinements": 1,
            "program_replays": 2,
            "unautomatable": unautomatable,
            "analyzer": {"calls": 1, "prompt_tokens": 100, "cached_tokens": 0,
                         "completion_tokens": 100, "total_tokens": 200, "cost_usd": usd(2_000)},
            "resume_episodes": {"calls": 4, "prompt_tokens": 2000, "cached_tokens": 1000,
                                "completion_tokens": 1000, "total_tokens": 3000, "cost_usd": usd(30_000)},
            "builder_refinements": {"calls": 1, "prompt_tokens": 300, "cached_tokens": 0,
                                    "completion_tokens": 200, "total_tokens": 500, "cost_usd": usd(5_000)},
            "gate_after_repair": gate_after,
            "totals_excluding_refinements": {"calls": 5, "prompt_tokens": 2100,
                                             "cached_tokens": 1000, "completion_tokens": 1100,
                                             "total_tokens": 3200, "cost_usd": usd(37_000)},
        },
        "deploy": deploy,
        "doc_arm": {
            "doc_from": "k3_doc",
            "episodes": [{"seed": 4, "success": True, "steps": 2, **usage(50000, 10000, 60_000),
                          "model_calls": 2}],
            "success_count": 1,
            "totals": {"calls": 2, "prompt_tokens": 50000, "cached_tokens": 0,
                       "completion_tokens": 10000, "total_tokens": 60000,
                       "cost_usd": usd(60_000)},
            "L_doc_tokens_mean": 60000.0,
        },
    }
    if redeploy is not None:
        build["redeploy"] = redeploy
    if not no_total_cost:
        build["total_cost_usd"] = 999.0  # deliberately wrong: must never be used
    (cell / "build.json").write_text(json.dumps(build))

    # Per-call records for the episodes that have them.
    exploration = {"instances": []}
    for episode in episodes:
        directory = cell / "explore" / f"s{episode['seed']}_a{episode['attempt']}"
        directory.mkdir(exist_ok=True)
        prompt = episode["prompt_tokens"]
        lines = [
            {"step": 1, "usage": {"prompt_tokens": prompt // 4,
                                  "completion_tokens": episode["completion_tokens"] // 2}},
            {"step": 2, "usage": {"prompt_tokens": prompt - prompt // 4,
                                  "completion_tokens": episode["completion_tokens"] // 2}},
            {"success": episode["success"], "total_tokens": episode["total_tokens"]},
        ]
        (directory / "trajectory.jsonl").write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n"
        )
        exploration["instances"].append({
            "seed": episode["seed"],
            "attempts": [{"attempt": episode["attempt"],
                          "trajectory": str(directory / "trajectory.jsonl")}],
        })
    (cell / "explore" / "exploration.json").write_text(json.dumps(exploration))

    (cell / "doc_arm" / "s4" / "trajectory.jsonl").write_text(
        "\n".join(json.dumps(line) for line in [
            {"step": 1, "usage": {"prompt_tokens": 12500, "completion_tokens": 5000}},
            {"step": 2, "usage": {"prompt_tokens": 37500, "completion_tokens": 5000}},
            {"success": True, "total_tokens": 60000},
        ]) + "\n"
    )
    if "n" in deploy:
        (cell / "deploy.json").write_text(json.dumps({
            "n": deploy["n"],
            "uses": (
                [{"success": True, "error_type": None, "tokens": 100}] * deploy["success_count"]
                + [{"success": False, "error_type": "program_error", "tokens": 100}]
                * (deploy["n"] - deploy["success_count"] - 1)
                + [{"success": False, "error_type": "extraction_error", "tokens": 100}]
            ),
        }))
    return cell


# d comes from the bill: cost_usd / p_in / n.
DEPLOY_COST = usd(10 * 500.0)  # n = 10 uses, 500 billed tokens each
DEPLOYED = {"n": 10, "success_count": 8, "success_rate": 0.8, "d_tokens_mean": 50.0,
            "total_tokens": 500, "cost_usd": DEPLOY_COST}
SKIPPED = {"skipped": "the type was declared unautomatable"}
GATE_5 = {"bindings_passed": 5, "bindings_total": 5}
GATE_4 = {"bindings_passed": 4, "bindings_total": 5}
GATE_3 = {"bindings_passed": 3, "bindings_total": 5}


def test_headline_unit_is_price_weighted_and_floor(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    record = ct.build_cell_record(cell)

    # Corrected sheet: GLM ratios unchanged, DeepSeek's are not 0.318 / 30.
    assert record["prices"]["p_in"] == 1.5e-7
    assert record["prices"]["r_c"] == pytest.approx(0.20)
    assert record["prices"]["r_o"] == pytest.approx(10.0 / 3.0)
    ds = ct.PRICE_SHEET["deepseek/deepseek-v4-flash-vision-exp"]
    assert ds["p_c"] / ds["p_in"] == pytest.approx(0.0318, abs=1e-4)
    assert ds["p_o"] / ds["p_in"] == pytest.approx(3.0)

    # The floor is uncached prompt, so its price-weighted value is the raw one.
    assert record["headline"]["floor_pw"] == pytest.approx(FLOOR)

    # Every stage is the counts formula on its recorded split.
    exploration = pw(104000, 20000, 11000)
    assert record["exploration"]["tokens_total_pw"] == pytest.approx(exploration)
    assert record["translator"]["tokens_pw"] == pytest.approx(pw(400, 0, 100))
    assert record["verification"]["resume_episodes_tokens_pw"] == pytest.approx(
        pw(2000, 1000, 1000)
    )
    assert record["doc_arm"]["tokens_total_pw"] == pytest.approx(pw(50000, 0, 10000))

    # c is the exploration stage total (episodes + reflection calls) per attempt.
    assert record["exploration"]["c_attempt_pw"] == pytest.approx(exploration / 4)
    assert record["headline"]["c_unsubtracted"] == pytest.approx(exploration / 4)
    assert record["headline"]["c"] == pytest.approx(exploration / 4 - FLOOR)
    # The episodes-only reading drops the reflection call.
    episodes = sum(pw(p, 0, c) for p, c in
                   ((10000, 1000), (20000, 2000), (30000, 3000), (40000, 4000)))
    assert record["exploration"]["c_attempt_pw_episodes_only"] == pytest.approx(episodes / 4)
    assert record["exploration"]["c_success_pw"] == pytest.approx(
        (pw(10000, 0, 1000) + pw(20000, 0, 2000)) / 2
    )

    # L_doc is floor-subtracted too.
    assert record["headline"]["L_doc"] == pytest.approx(pw(50000, 0, 10000) - FLOOR)
    assert record["headline"]["L_doc_unsubtracted"] == pytest.approx(pw(50000, 0, 10000))

    # C = translator + selected initial + refinements + analyzer + resume.
    assert record["compile_price"]["C_without_repair_pw"] == pytest.approx(
        pw(400, 0, 100) + pw(600, 0, 400)
    )
    expected_c = (pw(400, 0, 100) + pw(600, 0, 400) + pw(300, 0, 200)
                  + pw(100, 0, 100) + pw(2000, 1000, 1000))
    assert record["compile_price"]["C_with_repair_pw"] == pytest.approx(expected_c)
    assert record["headline"]["C_with_repair"] == pytest.approx(expected_c)

    # d is the one quantity taken from the bill: the deployment stage of these
    # cells records no prompt/cached/completion split.
    assert record["headline"]["d"] == pytest.approx(500.0)
    assert record["deploy"]["d_from_cost"] is True
    assert record["secondary_units"]["raw"]["d"] == 50.0


def test_reused_episode_cache_imputation(tmp_path):
    """A reused t12 episode has no cached_tokens; impute the cell's own share."""
    root = tmp_path / "t16"
    cell = write_cell(root, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    build = json.loads((cell / "build.json").read_text())
    episodes = build["exploration"]["per_episode"]
    # Seeds 1 and 2 come from t12_grid: reused, cached_tokens not reported.
    episodes[0]["reused"] = True
    episodes[1]["reused"] = True
    # The evidence is the two fresh attempts plus the doc arm: 35000 of 70000
    # and 25000 of 50000, a 50% share over the two together.
    episodes[2]["cached_tokens"] = 15000
    episodes[3]["cached_tokens"] = 20000
    build["exploration"]["totals"]["cached_tokens"] = 35000
    build["doc_arm"]["totals"]["cached_tokens"] = 25000
    (cell / "build.json").write_text(json.dumps(build))
    record = ct.build_cell_record(cell)
    exploration = record["exploration"]

    assert exploration["reused_episodes_cache_imputed"] == 2
    assert exploration["reused_cache_share_used"] == pytest.approx(0.5)
    assert exploration["reused_cache_share_source"] == "cell fresh+doc"
    # 50% of the two reused prompts, 10000 and 20000.
    assert exploration["reused_cache_tokens_imputed"] == pytest.approx(15000)
    assert exploration["episodes"][0]["pw_tokens"] == pytest.approx(pw(10000, 5000, 1000))

    # The stage total gains the imputed cache once: build.json summed those
    # prompts with cached 0, so 35000 becomes 50000.
    assert exploration["tokens_total_pw"] == pytest.approx(pw(104000, 50000, 11000))
    assert exploration["tokens_total_pw_no_imputation"] == pytest.approx(
        pw(104000, 35000, 11000)
    )
    assert exploration["c_attempt_pw"] == pytest.approx(pw(104000, 50000, 11000) / 4)
    assert exploration["c_attempt_pw_no_imputation"] == pytest.approx(
        pw(104000, 35000, 11000) / 4
    )
    assert exploration["c_attempt_pw"] < exploration["c_attempt_pw_no_imputation"]
    # The bill is untouched: an independent bound on the same episodes.
    assert exploration["tokens_total_usd_over_p_in"] == pytest.approx(120_000)
    assert exploration["reused_episodes_usd_over_p_in"] == pytest.approx(30_000)
    # The doc arm alone carries a cell whose exploration is all reused.
    doc_only = write_cell(root, "z-ai_glm-5.3-flash", "FamDocOnly",
                          gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    build = json.loads((doc_only / "build.json").read_text())
    for episode in build["exploration"]["per_episode"]:
        episode["reused"] = True
    build["doc_arm"]["totals"]["cached_tokens"] = 20000  # 20000 of 50000
    (doc_only / "build.json").write_text(json.dumps(build))
    record_doc = ct.build_cell_record(doc_only)
    assert record_doc["exploration"]["reused_cache_share_source"] == "cell doc"
    assert record_doc["exploration"]["reused_cache_share_used"] == pytest.approx(0.4)

    # A cell with neither falls back to the model's mean share.
    every = write_cell(root, "z-ai_glm-5.3-flash", "FamAllReused",
                       gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    build = json.loads((every / "build.json").read_text())
    for episode in build["exploration"]["per_episode"]:
        episode["reused"] = True
    build["doc_arm"]["totals"]["prompt_tokens"] = 0
    (every / "build.json").write_text(json.dumps(build))
    table = ct.build_table(root)
    fallback = next(c for c in table["cells"] if c["family"] == "FamAllReused")
    assert fallback["exploration"]["reused_cache_share_source"] == "model mean"
    # Evidence across the three cells: 35000 + 25000 + 20000 cached over
    # (70000 + 50000) + 50000 prompt; FamAllReused itself contributes nothing.
    assert fallback["exploration"]["reused_cache_share_used"] == pytest.approx(
        80_000 / 170_000
    )
    assert fallback["exploration"]["reused_episodes_cache_imputed"] == 4
    assert "reused" in table["conventions"]["reused_episode_cache_imputation"]


def test_usd_over_p_in_secondary_and_diagnostic(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    record = ct.build_cell_record(cell)
    bill = record["secondary_units"]["usd_over_p_in"]

    # The bill-based unit is cost_usd / p_in, stage by stage.
    assert record["exploration"]["tokens_total_usd_over_p_in"] == pytest.approx(120_000)
    assert bill["c_attempt"]["c"] == pytest.approx(30_000)
    assert bill["L_doc"] == pytest.approx(60_000)
    assert bill["C_with_repair"] == pytest.approx(52_000)
    assert bill["d"] == pytest.approx(500.0)

    # The diagnostic is pw / bill, per stage and per cell; never applied.
    stages = record["counts_diagnostic"]["per_stage"]
    assert stages["translator"]["usd_over_p_in"] == pytest.approx(5_000)
    assert stages["translator"]["pw"] == pytest.approx(pw(400, 0, 100))
    assert stages["translator"]["ratio_pw_over_usd"] == pytest.approx(pw(400, 0, 100) / 5_000)
    # The deployment stage has no split, so it is out of the cell ratio.
    assert stages["deploy"]["pw"] is None
    assert record["counts_diagnostic"]["usd_over_p_in_total"] == pytest.approx(
        120_000 + 5_000 + 12_000 + 2_000 + 30_000 + 5_000 + 60_000
    )
    assert record["counts_diagnostic"]["ratio_pw_over_usd"] == pytest.approx(
        record["counts_diagnostic"]["pw_total"]
        / record["counts_diagnostic"]["usd_over_p_in_total"]
    )


def test_missing_cached_tokens_is_counted(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    build = json.loads((cell / "build.json").read_text())
    del build["verification"]["analyzer"]["cached_tokens"]
    build["verification"]["analyzer"]["calls"] = 3
    (cell / "build.json").write_text(json.dumps(build))
    record = ct.build_cell_record(cell)

    assert record["cached_tokens_missing_calls"] == 3
    # Read as 0: the analyzer is charged as a fully fresh prompt.
    assert record["verification"]["analyzer_tokens_pw"] == pytest.approx(pw(100, 0, 100))
    assert any("cached_tokens" in note for note in record["notes"])


def test_secondary_units_kept(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    record = ct.build_cell_record(cell)

    # raw unit
    assert record["exploration"]["c_attempt_raw"] == 115000 / 4
    assert record["exploration"]["c_success_raw"] == (11000 + 22000) / 2
    assert record["compile_price"]["C_with_repair_raw"] == 500 + 1000 + 500 + 200 + 3000
    assert record["compile_price"]["C_with_repair_all_arms_raw"] == 1200 + 500 + 200 + 3000 + 500

    # deterministic cache-adjusted unit, unchanged: episode of two calls with
    # prompts 2500 then 7500, completions 500 and 500.
    first = record["exploration"]["episodes"][0]["cache_adjusted_deterministic_tokens"]
    assert first == 2500 + 500 + (7500 - 2500) + R * 2500 + 500
    assert record["compile_price"]["C_with_repair_cache_adjusted_deterministic"] is None
    assert (record["compile_price"]["C_with_repair_cache_adjusted_deterministic_upper"]
            == record["compile_price"]["C_with_repair_raw"])
    assert any("resume_episodes" in note
               for note in record["missing_cache_adjusted_deterministic"])


def test_edge_branches_of_the_deterministic_rule():
    assert ct.episode_effective_tokens([], 0.2)["effective_tokens"] == 0
    single = ct.episode_effective_tokens([(100, 10)], 0.2)
    assert single["effective_tokens"] == 110
    shrunk = ct.episode_effective_tokens([(100, 10), (50, 5)], 0.2)
    assert shrunk["effective_tokens"] == 110 + 55
    assert shrunk["prompt_shrank_calls"] == 1
    repeated = ct.episode_effective_tokens([(100, 10), (100, 5)], 0.2)
    assert repeated["effective_tokens"] == 110 + 0.2 * 100 + 5
    assert repeated["prompt_repeated_calls"] == 1


def test_admission_threshold_and_pending_redeploy(tmp_path):
    root = tmp_path / "t16"
    five = write_cell(root, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    four = write_cell(root, "z-ai_glm-5.3-flash", "Fam4of5",
                      gate_after=GATE_4, unautomatable=False, deploy=DEPLOYED)
    three = write_cell(root, "z-ai_glm-5.3-flash", "Fam3of5",
                       gate_after=GATE_3, unautomatable=False, deploy=DEPLOYED)
    # Unautomatable with a k=3 initial gate that cleared the threshold.
    pend = write_cell(root, "z-ai_glm-5.3-flash", "FamPending",
                      gate_after=None, unautomatable=True, deploy=SKIPPED, gate_k3=5)
    # Unautomatable with a k=3 initial gate that did not.
    dead = write_cell(root, "z-ai_glm-5.3-flash", "FamUnauto",
                      gate_after=None, unautomatable=True, deploy=SKIPPED, gate_k3=1)

    r5, r4, r3, rp, r0 = (ct.build_cell_record(c) for c in (five, four, three, pend, dead))

    assert ct.GATE_MIN_PASS == 4
    assert (r5["headline"]["p"], r5["headline"]["admitted"]) == (1.0, True)
    assert (r4["headline"]["p"], r4["headline"]["admitted"]) == (0.8, True)
    assert (r3["headline"]["p"], r3["headline"]["admitted"]) == (0.6, False)
    assert (r0["headline"]["p"], r0["headline"]["admitted"]) == (0.0, False)

    # Pending redeploy: p from the k=3 initial gate, q and d empty, admitted None.
    assert rp["gate"]["admission_pending_redeploy"] is True
    assert rp["headline"]["p"] == 1.0
    assert rp["headline"]["admitted"] is None
    assert rp["headline"]["q"] is None and rp["headline"]["d"] is None
    assert rp["headline"]["s_arrival"] is None
    assert rp["headline"]["nstar_marginal"] is None
    assert r0["gate"]["admission_pending_redeploy"] is False

    # Older admission readings are kept.
    assert (r5["gate"]["admitted_5of5"], r4["gate"]["admitted_5of5"]) == (True, False)
    assert (r5["gate"]["admitted_protocol"], r0["gate"]["admitted_protocol"]) == (True, False)
    assert r5["gate"]["p_initial_k2"] == 0.4


def test_redeploy_section_is_the_admission_evidence(tmp_path):
    """The redeploy path re-gates a discarded artifact and deploys it.

    It leaves verification.unautomatable true and gate_after_repair null, so
    the redeploy gate and the deploy section it wrote carry the admission.
    """
    root = tmp_path / "t16"
    redeploy = {
        "source_artifact": "/tmp/artifact_k3_code.py",
        "gate": {"bindings_passed": 4, "bindings_total": 5, "detail": []},
        "p_after": 0.8,
        "admitted": True,
        "gate_min_pass": ct.GATE_MIN_PASS,
        "timestamp": "2026-09-12T10:00:00+0000",
    }
    cell = write_cell(root, "z-ai_glm-5.3-flash", "FamRedeployed",
                      gate_after=None, unautomatable=True, deploy=DEPLOYED,
                      gate_k3=5, redeploy=redeploy)
    record = ct.build_cell_record(cell)
    gate, head = record["gate"], record["headline"]

    assert gate["admission_source"] == "redeploy"
    assert gate["redeploy_present"] is True
    assert gate["admission_pending_redeploy"] is False
    assert gate["p_after_repair"] == 0.8
    assert head["p"] == 0.8
    assert head["admitted"] is True
    # q and d come from the deploy section the redeploy run wrote.
    assert head["q"] == pytest.approx(0.2)
    assert head["d"] == pytest.approx(500.0)
    assert head["C_eff_c_over_p"] == pytest.approx(head["C_with_repair"] / 0.8)
    # The legacy protocol reading still follows verification.unautomatable.
    assert gate["admitted_protocol"] is False
    assert any("redeploy gate" in note for note in record["notes"])
    assert not any("deployed although" in note for note in record["notes"])

    # A failed redeploy is a non-admission, not a pending cell.
    failed = dict(redeploy, gate={"bindings_passed": 2, "bindings_total": 5, "detail": []},
                  p_after=0.4, admitted=False)
    cell2 = write_cell(root, "z-ai_glm-5.3-flash", "FamRedeployFailed",
                       gate_after=None, unautomatable=True, deploy=SKIPPED,
                       gate_k3=5, redeploy=failed)
    record2 = ct.build_cell_record(cell2)
    assert record2["headline"]["p"] == 0.4
    assert record2["headline"]["admitted"] is False
    assert record2["gate"]["admission_pending_redeploy"] is False

    # record2 is the model's only not-admitted cell, so it prices the failures.
    population = ct.apply_effective_price([record, record2])[record["model"]]
    c_fail = record2["headline"]["C_with_repair"]
    assert population["C_fail"] == pytest.approx(c_fail)
    assert population["admission_rate"] == pytest.approx(0.5)
    # One attempt observed, and it was admitted: C_eff is the price it paid.
    assert head["C_eff"] == pytest.approx(head["C_with_repair"])
    assert head["p_attempt"] == 1.0
    assert head["gate_margin"] == 0.8
    assert _finite(head["nstar_marginal"])
    # The gate-margin reading is kept beside it.
    assert head["C_eff_gate_margin"] == pytest.approx(
        head["C_with_repair"] + (1 / 0.8 - 1) * c_fail
    )

    summary = ct.aggregate([record, record2])
    assert summary["cells_redeployed"] == 2
    assert summary["cells_admitted_via_redeploy"] == 1
    assert summary["cells_admission_pending_redeploy"] == 0


def test_savings_and_break_even_in_the_headline_unit(tmp_path):
    root = tmp_path / "t16"
    write_cell(root, "z-ai_glm-5.3-flash", "Fam4of5",
               gate_after=GATE_4, unautomatable=False, deploy=DEPLOYED)
    write_cell(root, "z-ai_glm-5.3-flash", "FamUnauto",
               gate_after=None, unautomatable=True, deploy=SKIPPED, gate_k3=0)
    table = ct.build_table(root)
    record = next(c for c in table["cells"] if c["family"] == "Fam4of5")
    failed = next(c for c in table["cells"] if c["family"] == "FamUnauto")
    head = record["headline"]

    c = head["c"]
    assert head["q"] == pytest.approx(0.2)
    assert head["d"] == pytest.approx(500.0)
    assert head["s_arrival"] == pytest.approx(0.8 * c - 500.0)
    assert head["share_prog"] == pytest.approx((0.8 * c - 500.0) / c)
    assert head["s_doc"] == pytest.approx(c - head["L_doc"])

    # One compile attempt per cell: an admitted cell pays the price it paid.
    compile_price = head["C_with_repair"]
    c_fail = failed["headline"]["C_with_repair"]
    exploration_total = record["exploration"]["tokens_total_pw"]
    assert head["gate_margin"] == 0.8 and head["p_attempt"] == 1.0
    assert head["C_eff"] == pytest.approx(compile_price)
    assert head["nstar_marginal"] == pytest.approx(compile_price / head["s_arrival"])
    assert head["nstar_build"] == pytest.approx(
        (exploration_total + compile_price) / head["s_arrival"]
    )
    # A not-admitted cell is infinite in both.
    assert failed["headline"]["p_attempt"] == 0.0
    assert failed["headline"]["C_eff"] == ct.INF
    # The gate-margin and the C / gate readings are kept beside the headline.
    assert head["C_fail"] == pytest.approx(c_fail)
    assert head["C_eff_gate_margin"] == pytest.approx(compile_price + (1 / 0.8 - 1) * c_fail)
    assert head["nstar_marginal_gate_margin"] == pytest.approx(
        head["C_eff_gate_margin"] / head["s_arrival"]
    )
    assert head["C_eff_c_over_p"] == pytest.approx(compile_price / 0.8)
    assert head["nstar_build_c_over_p"] == pytest.approx(
        (exploration_total + compile_price) / head["s_arrival"]
    )

    # Population reading: the price of the NEXT family from this population.
    population = table["population"][record["model"]]
    assert population["admission_rate"] == pytest.approx(0.5)  # 1 of 2 cells
    assert population["C_admitted_median"] == pytest.approx(compile_price)
    assert population["C_fail"] == pytest.approx(c_fail)
    assert population["C_eff_population"] == pytest.approx(compile_price + c_fail)
    assert population["s_median"] == pytest.approx(head["s_arrival"])
    assert population["nstar_population"] == pytest.approx(
        (compile_price + c_fail) / head["s_arrival"]
    )
    assert record["deploy"]["program_error_failures"] == 1
    assert record["deploy"]["other_failures"] == 1


def test_per_success_appendix(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    record = ct.build_cell_record(cell)
    ct.apply_effective_price([record])  # p = 1, so C_eff = C_with_repair
    head, app = record["headline"], record["per_success_appendix"]

    c, q, d, pi = head["c"], head["q"], head["d"], head["pi"]
    assert pi == 0.5
    assert app["reactive_per_delivered_success"] == pytest.approx(c / pi)
    assert app["program_per_delivered_success"] == pytest.approx(
        (d + q * c) / ((1 - q) + q * pi)
    )
    s_success = app["reactive_per_delivered_success"] - app["program_per_delivered_success"]
    assert app["s_success"] == pytest.approx(s_success)
    assert app["nstar_success"] == pytest.approx(head["C_eff"] / s_success)


def test_break_even_is_infinite_when_p_is_zero(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "FamUnauto",
                      gate_after=None, unautomatable=True, deploy=DEPLOYED, gate_k3=0)
    record = ct.build_cell_record(cell)
    ct.apply_effective_price([record])
    assert record["headline"]["p"] == 0.0
    assert record["headline"]["C_eff"] == ct.INF
    assert record["headline"]["nstar_marginal"] == ct.INF
    # N*_build now carries C_eff too, so p = 0 makes it infinite as well; the
    # old C / p reading, which put the plain C in the numerator, stays finite.
    assert record["headline"]["nstar_build"] == ct.INF
    assert _finite(record["headline"]["nstar_build_c_over_p"])


def _finite(value) -> bool:
    return isinstance(value, float)


def test_pw_over_usd_diagnostic_band(tmp_path):
    cell = write_cell(tmp_path, "z-ai_glm-5.3-flash", "Fam5of5",
                      gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    build = json.loads((cell / "build.json").read_text())
    # Bill every stage exactly at the list sheet: the diagnostic must be 1.
    for totals in (build["exploration"]["totals"], build["translator"]["totals"],
                   build["builder"]["totals_all_calls"], build["verification"]["analyzer"],
                   build["verification"]["resume_episodes"],
                   build["verification"]["builder_refinements"], build["doc_arm"]["totals"]):
        totals["cost_usd"] = P_IN * pw(totals["prompt_tokens"], totals["cached_tokens"],
                                           totals["completion_tokens"])
    (cell / "build.json").write_text(json.dumps(build))
    record = ct.build_cell_record(cell)

    assert record["counts_diagnostic"]["ratio_pw_over_usd"] == pytest.approx(1.0)
    assert record["counts_diagnostic"]["outside_band"] is False
    assert record["counts_diagnostic"]["per_stage"]["translator"][
        "ratio_pw_over_usd"] == pytest.approx(1.0)

    # A bill ten times the sheet drops the ratio out of the band.
    for totals in (build["exploration"]["totals"], build["doc_arm"]["totals"]):
        totals["cost_usd"] *= 10
    (cell / "build.json").write_text(json.dumps(build))
    outside = ct.build_cell_record(cell)
    assert outside["counts_diagnostic"]["outside_band"] is True
    # It is a diagnostic: it never becomes a note and never changes a headline.
    assert not any("diagnostic" in note for note in outside["notes"])


def test_cost_is_summed_and_ignores_total_cost_usd(tmp_path):
    expected = usd(120_000 + 5_000 + 12_000 + 2_000 + 30_000 + 5_000 + 60_000) + DEPLOY_COST
    with_field = ct.build_cell_record(
        write_cell(tmp_path / "a", "z-ai_glm-5.3-flash", "Fam5of5",
                   gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED))
    without_field = ct.build_cell_record(
        write_cell(tmp_path / "b", "z-ai_glm-5.3-flash", "Fam5of5",
                   gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED,
                   no_total_cost=True))
    assert with_field["cost_usd"]["total_summed"] == pytest.approx(expected)
    assert without_field["cost_usd"]["total_summed"] == pytest.approx(expected)
    assert without_field["cost_usd"]["total_recorded_field"] is None
    # The wrong recorded field is reported as a disagreement, never used.
    assert any("total_cost_usd" in note for note in with_field["notes"])


def test_table_end_to_end_and_spoiled_dirs_skipped(tmp_path):
    root = tmp_path / "t16"
    write_cell(root, "z-ai_glm-5.3-flash", "Fam5of5",
               gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)
    write_cell(root, "z-ai_glm-5.3-flash", "FamPending",
               gate_after=None, unautomatable=True, deploy=SKIPPED, gate_k3=5)
    write_cell(root, "z-ai_glm-5.3-flash", "FamUnauto",
               gate_after=None, unautomatable=True, deploy=SKIPPED, gate_k3=0)
    write_cell(root, "z-ai_glm-5.3-flash", "FamOld.spoiled-20260911-0354",
               gate_after=GATE_5, unautomatable=False, deploy=DEPLOYED)

    table = ct.build_table(root)
    assert [c["family"] for c in table["cells"]] == ["Fam5of5", "FamPending", "FamUnauto"]
    summary = table["per_model"]["z-ai_glm-5.3-flash"]
    assert summary["cells"] == 3
    assert summary["cells_deployed"] == 1
    assert summary["cells_admitted"] == 1
    assert summary["cells_admission_pending_redeploy"] == 1
    assert summary["quantities"]["headline.pi"]["mean"] == 0.5
    # inf values are counted apart from the mean, never folded into it.
    marginal = summary["quantities"]["headline.nstar_marginal"]
    assert marginal["n_defined"] == 1 and marginal["n_infinite"] == 1
    assert "price-weighted tokens (pw)" in table["conventions"]["unit"]
    assert table["price_sheet_fetched"] == ct.PRICE_SHEET_FETCHED
    assert "cost_usd / p_in" in table["conventions"]["usd_over_p_in"]
    assert "under-count" in table["conventions"]["C_with_repair"].lower()

    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    assert ct.main(["--root", str(root), "--out-json", str(out_json),
                    "--out-md", str(out_md)]) == 0
    json.loads(out_json.read_text())  # strict JSON: no NaN, no Infinity
    text = out_md.read_text()
    assert "FamUnauto" in text and "inf" in text and "—" in text
    assert "pending" in text
    assert "Per-success appendix" in text

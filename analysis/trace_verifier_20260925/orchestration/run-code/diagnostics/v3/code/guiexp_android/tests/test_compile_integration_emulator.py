"""Integration test of the compile path against the real emulator (LLM-free).

One family -- ContactsAddContact, the simplest form. The chain, with every
model call replaced by a deterministic stub (MockOpenAI for the source
episode, MockCompiler for the compile, MockExtractor for deployment):

  1. record a REAL trajectory by running a scripted discover action list
     through the live env (runner + MockOpenAI),
  2. MockCompiler -> a replay program parameterized over {name, number},
  3. gate it on 5 held-out bindings judged by android_world's own
     contacts oracle (must pass >= 4/5), plus the non-vacuity probe,
  4. run the deploy chain (extract -> type check -> program -> oracle) on
     10 uses (must pass >= 8/10).

Zero LLM calls. Emulator etiquette mirrors test_integration_emulator.py:
boot AndroidWorldAvd only if none is attached; kill it at session end ONLY
if this session started it. Set ANDROID_EXP_SKIP_EMULATOR=1 to skip.
"""

from __future__ import annotations

import os
import time

import pytest

from guiexp_android import android_env, gate_runner
from guiexp_android.compiler import MockCompiler, load_trajectory, trajectory_instance
from guiexp_android.deploy_runner import MockExtractor, deploy_uses, run_deployment
from guiexp_android.gate_runner import heldout_bindings, run_gate
from guiexp_android.mock_model import MockOpenAI
from guiexp_android.program_runtime import ProgramRunner, program_from_source
from guiexp_android.runner import run_episode

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("ANDROID_EXP_SKIP_EMULATOR") == "1",
        reason="ANDROID_EXP_SKIP_EMULATOR=1",
    ),
    pytest.mark.skipif(
        not android_env.SDK_DIR.exists(),
        reason=f"Android SDK not found at {android_env.SDK_DIR}",
    ),
]

FAMILY = "ContactsAddContact"

# A scripted discover run for Contacts seed 0 that avoids the cold-boot
# fragile home-screen/dialer route: open the Contacts app directly (the
# empty-state list exposes a 'Create contact' control at index 0 after
# initialize_task clears all contacts), then fill the editor exactly as the
# GLM run did. Verified against a cold-booted emulator.
SCRIPT = [
    '{"action_type": "open_app", "app_name": "Contacts"}',
    '{"action_type": "click", "index": 0}',
    '{"action_type": "input_text", "index": 7, "text": "Louis"}',
    '{"action_type": "input_text", "index": 8, "text": "Lopez"}',
    '{"action_type": "input_text", "index": 10, "text": "+10487647593"}',
    '{"action_type": "click", "index": 2}',
]


@pytest.fixture(scope="module")
def emulator():
    """Boot the emulator only if none is attached; kill only if we booted."""
    booted = False
    if not android_env.emulator_is_running():
        android_env.boot_emulator()
        booted = True
    yield
    if booted:
        android_env.shutdown_emulator()


@pytest.fixture(scope="module")
def recorded_trajectory(emulator, tmp_path_factory):
    """A real recorded trajectory: scripted model, live env.

    The env settles 4.5 s per action (vs the reactive default 2 s): the
    Contacts app's FIRST launch after a cold emulator boot animates in
    slower than the settle, and a click fired mid-animation lands on the
    previous screen's element list."""
    for attempt in (1, 2):
        out_dir = tmp_path_factory.mktemp(f"episode{attempt if attempt > 1 else ''}")
        episode_env = android_env.AndroidWorldEnv(wait_after_action_seconds=4.5)
        try:
            final = run_episode(
                family=FAMILY,
                condition="discover",
                seed=0,
                model="mock",
                obs_mode="screenshot+ax",
                max_steps=10,
                out_dir=out_dir,
                client=MockOpenAI(script=SCRIPT),
                env=episode_env,
                keep_emulator=True,  # the module-scoped fixture owns the lifecycle
            )
        finally:
            episode_env.close()
        if final["success"]:
            return out_dir / "trajectory.jsonl"
        time.sleep(10)  # cold-boot a11y/app warm-up; one retry
    pytest.fail(f"scripted episode never succeeded: {final}")


@pytest.fixture(scope="module")
def compiled(recorded_trajectory):
    result = MockCompiler().compile(recorded_trajectory, FAMILY)
    _module, program = program_from_source(result["program_source"])
    return {"result": result, "program": program, "trajectory": recorded_trajectory}


@pytest.fixture(scope="module")
def runner(emulator):
    # 4.5 s per-action settle: the Contacts app on this AVD needs it (see
    # recorded_trajectory); programs then settle identically via ProgramDevice.
    env = android_env.AndroidWorldEnv(wait_after_action_seconds=4.5)
    yield ProgramRunner(env)
    env.close()


def test_recorded_trajectory_is_the_scripted_success(recorded_trajectory):
    steps, final = load_trajectory(recorded_trajectory, FAMILY)
    assert final["success"] is True
    assert [s["action"]["action_type"] for s in steps].count("input_text") == 3
    # the typed values are the seed-0 instance's
    instance = trajectory_instance(FAMILY, final)
    assert instance["name"].split()[0] in recorded_trajectory.read_text()


def test_gate_five_heldout_bindings(compiled, runner):
    _steps, final = load_trajectory(compiled["trajectory"], FAMILY)
    instance = trajectory_instance(FAMILY, final)
    draws = heldout_bindings(FAMILY, 5, exclude_params=instance, seed=0)
    gate = run_gate(compiled["program"], FAMILY, draws, runner)
    assert gate["bindings_total"] == 5
    assert gate["bindings_passed"] >= 4, gate["detail"]
    # every passed binding actually created its own (different) contact
    passed = [d["binding"] for d in gate["detail"] if d["passed"]]
    assert len({b["name"] for b in passed}) == len(passed)

    # the gate is not vacuous: a program that does nothing must fail it
    def noop(device, binding):
        device.navigate_home()

    empty = run_gate(noop, FAMILY, draws[:1], runner)
    assert empty["bindings_passed"] == 0


def test_deployment_mock_chain(compiled, runner):
    summary = run_deployment(
        compiled["program"], FAMILY, deploy_uses(FAMILY, 10), MockExtractor(FAMILY),
        "mock", runner,
    )
    assert summary["n"] == 10
    assert summary["success_count"] >= 8, [u["error_type"] for u in summary["uses"]]
    assert summary["total_tokens"] > 0
    for use in summary["uses"]:
        assert use["tokens"] > 0  # the per-use extraction price d
        assert use["retries"] in (0, 1)
        assert use["error_type"] in (
            None, "extraction_json", "type_check", "program_error", "oracle_fail",
        )
        if use["success"]:
            assert use["extracted"] == use["expected"]
            assert use["error_type"] is None

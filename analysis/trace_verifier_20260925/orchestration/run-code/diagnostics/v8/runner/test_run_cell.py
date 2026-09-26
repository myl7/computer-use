import os

import run_cell


def test_load_env_propagates_base_url_and_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env = tmp_path / "private.env"
    env.write_text("OPENROUTER_BASE_URL=https://example.invalid/v1\nOPENROUTER_API_KEY=secret\n")
    run_cell.load_env(str(env))
    assert os.environ["OPENROUTER_BASE_URL"] == "https://example.invalid/v1"
    assert os.environ["OPENROUTER_API_KEY"] == "secret"


def test_t20_worker_error_and_zero_call_are_invalid():
    assert not run_cell.valid_t20_row({"status": "worker_error"})
    assert not run_cell.valid_t20_row({"status": "complete", "model_calls": 0,
                                      "extraction": {"calls_detail": []}})


def test_t20_complete_call_is_reusable_without_duplicate():
    row = {"status": "complete", "model_calls": 1,
           "extraction": {"calls_detail": [{"call": 1}]}}
    assert run_cell.valid_t20_row(row)


def test_t18_requires_complete_and_restored_fingerprint():
    clean = {"status": "complete", "entry": {"fingerprint": {
        "before": {"system.font_scale": None, "system.system_locales": None},
        "after": {"system.font_scale": "1.0", "system.system_locales": "en-US"},
        "reverted_clean": False}}}
    assert run_cell.valid_t18_row(clean)
    clean["entry"]["fingerprint"]["after"]["battery"] = {"level": 5}
    assert not run_cell.valid_t18_row(clean)

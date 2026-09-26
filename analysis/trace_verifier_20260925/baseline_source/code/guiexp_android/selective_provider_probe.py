"""One-shot, no-UI provider availability probe for the selective tranche."""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping
from . import selective_explore_budget as budget
from .budget_client import BudgetStop
from .selective_projected import PROVIDER_PROFILE_CHOICES, PROVIDER_REGISTRY
SCHEMA = "selective-provider-probe/1"
VERSION = "provider_probe_v1"
PROMPT = "Return exactly one JSON object with ok:true."
DEFAULT_PROVIDER_PROFILE = "deepinfra_fp4"
REQUEST_PROFILE = "serving_4096"
MODEL = budget.MODEL
def _provider(name: str) -> dict[str, Any]:
    try:
        return dict(PROVIDER_REGISTRY[str(name)])
    except (KeyError, TypeError):
        raise BudgetStop(f"Unsupported provider probe profile: {name!r}.") from None
def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
def _manifest(profile: str, phase: Mapping[str, Any], out: Path) -> dict[str, Any]:
    provider = _provider(profile)
    return {
        "schema": SCHEMA, "version": VERSION, "model": MODEL,
        "provider_profile": profile, "provider": provider["provider"],
        "provider_config": provider, "request_profile": REQUEST_PROFILE,
        "prompt": PROMPT, "shared_ledger": str(budget.SHARED_LEDGER_PATH),
        "shared_run_lock": str(budget.SHARED_RUN_LOCK_PATH),
        "authorization_path": str(budget.AUTHORIZATION_PATH),
        "source_sha256": _hash(Path(__file__).resolve()),
        "phase_manifest": {
            "path": "phase_manifest.json", "sha256": _hash(out / "phase_manifest.json"),
            "phase_manifest_sha256": phase.get("phase_manifest_sha256"),
        },
        "created_unix": time.time(),
    }
def prepare(out: Path | str, provider_profile: str = DEFAULT_PROVIDER_PROFILE) -> dict[str, Any]:
    """Freeze the serving profile without constructing a client."""
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        if (out / "manifest.json").is_file():
            return load(out)
        raise BudgetStop("Probe output contains an unarchived partial state.")
    _provider(provider_profile)
    out.mkdir(parents=True, exist_ok=True)
    phase = budget.freeze_phase_manifest(
        f"{VERSION}_run", model_locks=None, provider_profile=provider_profile,
        request_profile=REQUEST_PROFILE, path=out / "phase_manifest.json",
        authorization_path=budget.AUTHORIZATION_PATH,
        extra={"probe_version": VERSION, "prompt": PROMPT},
    )
    body = _manifest(provider_profile, phase, out)
    body["manifest_sha256"] = hashlib.sha256(budget.canonical(body).encode()).hexdigest()
    _write(out / "manifest.json", body)
    return body
def load(out: Path | str) -> dict[str, Any]:
    """Verify the probe and its ExploreBudget phase manifest."""
    out = Path(out).resolve()
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BudgetStop("Probe manifest is unavailable or invalid.") from exc
    body, digest = dict(manifest), manifest.get("manifest_sha256")
    body.pop("manifest_sha256", None)
    if manifest.get("schema") != SCHEMA or manifest.get("version") != VERSION:
        raise BudgetStop("Probe manifest schema or version changed.")
    if not isinstance(digest, str) or hashlib.sha256(budget.canonical(body).encode()).hexdigest() != digest:
        raise BudgetStop("Probe manifest hash changed.")
    profile = manifest.get("provider_profile")
    if manifest.get("provider_config") != _provider(profile) or manifest.get("provider") != _provider(profile)["provider"]:
        raise BudgetStop("Probe provider profile changed.")
    if manifest.get("request_profile") != REQUEST_PROFILE or manifest.get("prompt") != PROMPT:
        raise BudgetStop("Probe request profile or prompt changed.")
    if manifest.get("source_sha256") != _hash(Path(__file__).resolve()):
        raise BudgetStop("Probe source changed after prepare.")
    reference = manifest.get("phase_manifest") or {}
    phase = out / str(reference.get("path") or "")
    if not phase.is_file() or _hash(phase) != reference.get("sha256"):
        raise BudgetStop("Probe phase manifest changed.")
    budget.load_phase_manifest(phase)
    return manifest
def run(out: Path | str, *, env_path: Path | str | None = None,
        ledger: Any = None, sdk: Any = None, metadata_fetcher: Any = None,
        host_guard: Any = None) -> dict[str, Any]:
    """Issue exactly one serving-profile request and save raw result evidence."""
    out, manifest = Path(out).resolve(), load(out)
    state_path, result_path, raw_path, identity = (out / name for name in (
        "state.json", "result.json", "raw_result.json", "run_identity.json"))
    if any(path.exists() for path in (state_path, result_path, raw_path, identity)):
        raise BudgetStop("Probe has prior state or result evidence; no replay is safe.")
    guard = host_guard if host_guard is not None else budget.read_host_state
    target = ledger if ledger is not None else budget.make_ledger(
        authorization_path=manifest["authorization_path"], host_guard=guard)
    episode = f"{budget.EXPLORE_NAMESPACE}/provider_probe/{manifest['provider_profile']}"
    if target.has_episode(episode):
        raise BudgetStop("Probe episode already has a receipt; no replay is safe.")
    state = {"schema": SCHEMA, "status": "running", "episode": episode,
             "provider_profile": manifest["provider_profile"], "provider": manifest["provider"],
             "request_profile": REQUEST_PROFILE, "prompt": PROMPT,
             "manifest_sha256": manifest["manifest_sha256"], "raw_result_path": raw_path.name}
    _write(state_path, state)
    try:
        with budget.exclusive_run(identity_path=identity, mode=VERSION):
            client = budget.make_client(
                None, env_path, ledger=target, sdk=sdk, episode=episode,
                profile=REQUEST_PROFILE, provider_profile=manifest["provider_profile"],
                metadata_fetcher=metadata_fetcher, host_guard=guard,
                authorization_path=manifest["authorization_path"],
            )
            response = client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": PROMPT}], temperature=0.0)
            raw = dict(response) if isinstance(response, Mapping) else response.model_dump(mode="json")
            _write(raw_path, raw)
            summary = {"schema": SCHEMA, "status": "returned", "episode": episode,
                       "provider_profile": manifest["provider_profile"], "provider": manifest["provider"],
                       "request_profile": REQUEST_PROFILE, "raw_result_path": raw_path.name,
                       "generation_id": raw.get("id")}
            state.update(status="returned", result_path=result_path.name)
            _write(result_path, summary); _write(state_path, state)
            return summary
    except BudgetStop as exc:
        state.update(status="budget_stopped", error_type=type(exc).__name__); _write(state_path, state)
        raise
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__); _write(state_path, state)
        raise
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true"); group.add_argument("--run", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--provider-profile", choices=PROVIDER_PROFILE_CHOICES, default=DEFAULT_PROVIDER_PROFILE)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.out, args.provider_profile) if args.prepare else run(args.out, env_path=args.env_file)
        print(json.dumps({"status": result.get("status", "prepared"), "provider_profile": result.get("provider_profile"), "manifest_sha256": result.get("manifest_sha256")}, sort_keys=True))
        return 0
    except BudgetStop as exc:
        print(f"STOPPED: {type(exc).__name__}: {str(exc)}")
        return 2
if __name__ == "__main__":
    raise SystemExit(main())

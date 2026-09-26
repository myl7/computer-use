"""Bounded zero-model Markor filename-label characterization, version 2.

This keeps the v1 probe's four-file ownership and read-only ledger protocol,
then relaunches Markor once after pushing the files so its file list can
refresh.  It is an environment characterization probe, not a UI-agent or
task-success experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import re
import shlex
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import budget_client
from . import selective_explore_budget as explore_budget
from . import selective_budget
from . import selective_markor_label_probe as v1
from .budget_client import BudgetStop


VERSION, SCHEMA = "markor_label_probe_v2", "selective-markor-label-probe/2"
ROOT = v1.ROOT
ADB_PATH = v1.ADB_PATH
SERIAL, PACKAGE, VERSION_NAME, VERSION_CODE = (
    v1.SERIAL,
    v1.PACKAGE,
    v1.VERSION_NAME,
    v1.VERSION_CODE,
)
MARKOR_DATA = v1.MARKOR_DATA
SHARED_LEDGER_PATH = v1.SHARED_LEDGER_PATH
SHARED_RUN_LOCK_PATH = v1.SHARED_RUN_LOCK_PATH
DEFAULT_OUT = (
    ROOT / "experimental-results/guiexp_android/selective_20260915/markor_label_probe_v2"
).resolve()
TIMEOUT, MAX_SWIPES = v1.TIMEOUT, v1.MAX_SWIPES
SPEC_NAME, CLAIM_NAME, SUMMARY_NAME = "spec.json", "run_claim.json", "summary.json"
RELAUNCH_LIMIT = 1
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FORWARDER_PACKAGE = "com.google.androidenv.accessibilityforwarder"
FORWARDER_SERVICE_CLASS = (
    "com.google.androidenv.accessibilityforwarder.AccessibilityForwarder"
)
FORWARDER_COMPONENT = f"{FORWARDER_PACKAGE}/{FORWARDER_SERVICE_CLASS}"
SECURE_ENABLED_SERVICES = "enabled_accessibility_services"
SECURE_ACCESSIBILITY_ENABLED = "accessibility_enabled"
ACQUISITION_MODE = "native_uiautomator_reversible_forwarder_suspend_v1"
SUSPEND_COMMANDS = (
    "settings delete secure enabled_accessibility_services",
    "settings put secure accessibility_enabled 0",
    "am force-stop com.google.androidenv.accessibilityforwarder",
)
RESTORE_ORDER = (
    "owned_file_cleanup",
    "restore_secure_settings",
    "readback_secure_settings",
    "release_runner_lock",
)

# The v1 source and its imported runtime helpers are inputs to this
# version.  Keep their expected bytes explicit so a later source edit stops a
# prepared v2 run instead of silently changing what it reuses.
FROZEN_IMPORTED_SOURCE_SHA256 = {
    "probe_v1": "73781e47ec46f983fd38a8468c77e41e9c060053e9b3beceb294e1d26d8d13fd",
    "explore_budget": "f2b3a86a7d924132656c78385741dddb4327ebb998a02f052c1ffd8484c3431f",
    "budget_client": "ac5debcf5dd3ddd8c595ce953521ad3f69b381484366e0669b85d32a05b1bc9d",
}
FROZEN_V1_SPEC_SHA256 = "9871c09d352c241d56e51ea699be11b404494145be4797b780cc0f9df83581ee"
_IMPORTED_SOURCE_PATHS = {
    "probe_v1": Path(v1.__file__).resolve(),
    "explore_budget": Path(explore_budget.__file__).resolve(),
    "budget_client": Path(budget_client.__file__).resolve(),
    # `explore_budget.read_host_state` is an alias to this transitive guard.
    # Its bytes are captured in each spec and compared at load time because a
    # separate host-wake repair may change them before v2 is prepared.
    "selective_budget": Path(selective_budget.__file__).resolve(),
}

ProbeStop = v1.ProbeStop
Adb = v1.Adb
_dump = v1._dump
_hash = v1._hash
_fh = v1._fh
_write = v1._write
_text = v1._text
_raw = v1._raw
_names = v1._names
_browser = v1._browser
_component = v1._component
_activity = v1._activity
_package = v1._package
_version = v1._version
_size = v1._size
_host = v1._host
ledger_snapshot = v1.ledger_snapshot
extract_rows = v1.extract_rows


def _source_hashes() -> dict[str, str]:
    current = {
        name: _fh(path) for name, path in _IMPORTED_SOURCE_PATHS.items()
    }
    if any(
        current.get(name) != expected
        for name, expected in FROZEN_IMPORTED_SOURCE_SHA256.items()
    ):
        raise ProbeStop("an imported v1 helper source changed after freezing")
    current["probe_v2"] = _fh(Path(__file__).resolve())
    return current


def _runtime_identity() -> dict[str, Any]:
    return {
        "python": sys.version,
        "executable": sys.executable,
        "runtime_manifest": explore_budget.runtime_manifest(),
    }


def exact_command(out: Path | str = DEFAULT_OUT) -> str:
    return (
        "../.venv-android/bin/python -m guiexp_android."
        "selective_markor_label_probe_v2 --run --out "
        f"{shlex.quote(str(Path(out).resolve()))}"
    )


def prepare(out: Path | str = DEFAULT_OUT, *, suffix: str | None = None) -> dict[str, Any]:
    """Create a fresh v2 specification without touching v1 output."""
    out = Path(out).resolve()
    if (out / SPEC_NAME).is_file():
        return load_spec(out)
    if out.exists() and any(out.iterdir()):
        raise ProbeStop("probe output contains a partial draft")
    suffix = suffix or uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[0-9a-f]{8,64}", suffix):
        raise ProbeStop("probe suffix must be lowercase hexadecimal")

    base = "000_cua_probe_" + suffix
    names = _names(base)
    fixture_dir = out / "fixtures"
    bodies = {name: f"cua_probe_filename={name}\n" for name in names}
    for name, body in bodies.items():
        path = fixture_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    hashes = {name: _fh(fixture_dir / name) for name in names}
    source_hashes = _source_hashes()
    spec: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "basename": base,
        "filenames": names,
        "fixtures": {
            name: {
                "path": str((fixture_dir / name).resolve()),
                "sha256": hashes[name],
            }
            for name in names
        },
        "input_sha256": _hash({"bodies": bodies, "sha256": hashes}),
        "input_hashes": hashes,
        "source_sha256": source_hashes["probe_v2"],
        "source_hashes": source_hashes,
        "runtime": _runtime_identity(),
        "acquisition": {
            "mode": ACQUISITION_MODE,
            "forwarder_package": FORWARDER_PACKAGE,
            "forwarder_component": FORWARDER_COMPONENT,
            "secure_settings": [
                SECURE_ENABLED_SERVICES,
                SECURE_ACCESSIBILITY_ENABLED,
            ],
            "foreign_services": "stop_unchanged",
            "suspend": list(SUSPEND_COMMANDS),
            "restore_order": list(RESTORE_ORDER),
        },
        "cleanup_ownership_hashes": hashes,
        "targets": {name: f"{MARKOR_DATA}/{name}" for name in names},
        "expected": {
            "serial": SERIAL,
            "package": PACKAGE,
            "version_name": VERSION_NAME,
            "version_code": VERSION_CODE,
            "markor_data": MARKOR_DATA,
        },
        "shared_ledger": str(SHARED_LEDGER_PATH),
        "shared_run_lock": str(SHARED_RUN_LOCK_PATH),
        "prior_v1": {
            "version": v1.VERSION,
            "spec_sha256": FROZEN_V1_SPEC_SHA256,
            "output": str(v1.DEFAULT_OUT),
        },
        "execution": {
            "model_calls": 0,
            "task_success_claim": False,
            "max_swipes": MAX_SWIPES,
            "max_relaunches": RELAUNCH_LIMIT,
            "device_files": names,
            "cleanup_requires_body_hash": True,
            "run_claim_is_permanent": True,
        },
        "observation": {
            "requires_well_formed_hierarchy": True,
            "requires_nonempty_png": True,
            "requires_foreground_package_match": True,
            "relaunch_after_push": True,
            "auxiliary_settings_snapshot_and_restore": True,
        },
        "limitation": (
            "Privileged benchmark setup for Markor AX filename rendering; "
            "not a UI-agent method or task-success experiment."
        ),
        "cli": {
            "working_directory": "computer-use",
            "interpreter": "../.venv-android/bin/python",
            "command": exact_command(out),
        },
    }
    spec["spec_sha256"] = _hash(spec)
    _write(out / SPEC_NAME, spec)
    return spec


def load_spec(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    out = Path(out).resolve()
    try:
        value = json.loads((out / SPEC_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProbeStop("probe spec is unavailable or invalid") from exc
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA or value.get("version") != VERSION:
        raise ProbeStop("probe spec schema or version changed")
    body = {key: item for key, item in value.items() if key != "spec_sha256"}
    if value.get("spec_sha256") != _hash(body):
        raise ProbeStop("probe spec hash changed")
    source_hashes = _source_hashes()
    if value.get("source_sha256") != source_hashes["probe_v2"]:
        raise ProbeStop("probe v2 source changed after preparation")
    if value.get("source_hashes") != source_hashes:
        raise ProbeStop("probe imported source hashes changed after preparation")
    if value.get("runtime") != _runtime_identity():
        raise ProbeStop("probe runtime identity changed after preparation")
    acquisition = value.get("acquisition")
    if (
        not isinstance(acquisition, Mapping)
        or acquisition.get("mode") != ACQUISITION_MODE
        or acquisition.get("forwarder_package") != FORWARDER_PACKAGE
        or acquisition.get("forwarder_component") != FORWARDER_COMPONENT
        or acquisition.get("secure_settings")
        != [SECURE_ENABLED_SERVICES, SECURE_ACCESSIBILITY_ENABLED]
        or acquisition.get("foreign_services") != "stop_unchanged"
        or acquisition.get("suspend") != list(SUSPEND_COMMANDS)
        or acquisition.get("restore_order") != list(RESTORE_ORDER)
    ):
        raise ProbeStop("probe acquisition contract changed")
    if value.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH):
        raise ProbeStop("probe shared runner lock changed")
    if value.get("shared_run_lock") != str(explore_budget.SHARED_RUN_LOCK_PATH):
        raise ProbeStop("probe budget lock changed")
    if value.get("shared_ledger") != str(SHARED_LEDGER_PATH):
        raise ProbeStop("probe shared ledger changed")

    base, names = value.get("basename"), value.get("filenames")
    if not isinstance(base, str) or not base.startswith("000_cua_probe_") or names != _names(base):
        raise ProbeStop("probe filename contract changed")
    fixtures = value.get("fixtures")
    if not isinstance(fixtures, Mapping) or set(fixtures) != set(names):
        raise ProbeStop("probe fixtures are incomplete")
    bodies, body_hashes = {}, {}
    for name in names:
        item = fixtures[name]
        path = Path(str(item.get("path", ""))).resolve()
        if not path.is_file() or _fh(path) != item.get("sha256"):
            raise ProbeStop(f"probe fixture changed: {name}")
        bodies[name] = path.read_text(encoding="utf-8")
        body_hashes[name] = item["sha256"]
        if bodies[name] != f"cua_probe_filename={name}\n":
            raise ProbeStop(f"probe fixture body changed: {name}")
    if (
        value.get("input_sha256") != _hash({"bodies": bodies, "sha256": body_hashes})
        or value.get("input_hashes") != body_hashes
        or value.get("cleanup_ownership_hashes") != body_hashes
    ):
        raise ProbeStop("probe input hash changed")
    expected = {
        "serial": SERIAL,
        "package": PACKAGE,
        "version_name": VERSION_NAME,
        "version_code": VERSION_CODE,
        "markor_data": MARKOR_DATA,
    }
    if value.get("expected") != expected:
        raise ProbeStop("probe device or package expectation changed")
    if value.get("prior_v1", {}).get("spec_sha256") != FROZEN_V1_SPEC_SHA256:
        raise ProbeStop("v1 provenance changed")
    execution = value.get("execution")
    if (
        not isinstance(execution, Mapping)
        or execution.get("model_calls") != 0
        or execution.get("max_swipes") != MAX_SWIPES
        or execution.get("max_relaunches") != RELAUNCH_LIMIT
        or execution.get("device_files") != names
        or execution.get("cleanup_requires_body_hash") is not True
        or execution.get("run_claim_is_permanent") is not True
    ):
        raise ProbeStop("probe execution contract changed")
    return dict(value)


class _Run:
    """The v1 receipt runner with the v2 schema frozen into each receipt."""

    def __init__(self, out: Path, adb: Any, guard: Any):
        self.out, self.adb, self.guard, self.number = out, adb, guard, 0
        (out / "receipts").mkdir(parents=True, exist_ok=True)

    def call(self, label: str, *args: str, check: bool = True) -> Any:
        _host(self.guard)
        started = time.time()
        result, error = None, None
        try:
            result = self.adb.run(*args, check=False)
        except BaseException as exc:  # keep the receipt before failing closed
            error = f"{type(exc).__name__}: {exc}"
        self.number += 1
        stdout = _raw(getattr(result, "stdout", b""))
        stderr = _text(getattr(result, "stderr", ""))
        receipt = {
            "schema": SCHEMA,
            "label": label,
            "args": list(args),
            "started_unix": started,
            "ended_unix": time.time(),
            "returncode": getattr(result, "returncode", None),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stdout_bytes": len(stdout),
            "stderr": stderr,
            "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        }
        if len(stdout) <= 200_000:
            receipt["stdout"] = _text(stdout)
        if error:
            receipt["error"] = error
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80]
        _write(self.out / "receipts" / f"{self.number:04d}_{safe_label}.json", receipt)
        if error or (check and getattr(result, "returncode", 1) != 0):
            raise ProbeStop(f"{label} failed")
        return result


def _claim(out: Path, spec: Mapping[str, Any]) -> None:
    try:
        fd = os.open(out / CLAIM_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema": SCHEMA,
                    "version": VERSION,
                    "status": "claimed",
                    "pid": os.getpid(),
                    "spec_sha256": spec["spec_sha256"],
                    "claimed_unix": time.time(),
                },
                handle,
            )
            handle.write("\n")
    except FileExistsError as exc:
        raise ProbeStop("probe lifetime claim already exists; rerun is refused") from exc


def _absent(run: _Run, path: str) -> None:
    result = run.call("absence_check", "shell", "ls", "-ld", path, check=False)
    if getattr(result, "returncode", 1) == 0:
        raise ProbeStop(f"probe target already exists: {path}")
    if not re.search(r"No such file|cannot access", _text(getattr(result, "stderr", ""))):
        raise ProbeStop(f"probe target absence is ambiguous: {path}")


def _read_secure_setting(run: _Run, label: str, key: str) -> dict[str, Any]:
    result = run.call(label, "shell", "settings", "get", "secure", key)
    raw = _raw(getattr(result, "stdout", b""))
    if getattr(result, "returncode", 1) != 0:
        raise ProbeStop(f"{key} secure setting read failed")
    value = _text(raw).strip()
    if not value and key == SECURE_ENABLED_SERVICES:
        present, parsed = True, ""
    elif not value:
        raise ProbeStop(f"{key} secure setting read is ambiguous")
    elif value.lower() == "null":
        present, parsed = False, None
    else:
        present, parsed = True, value
        if key == SECURE_ACCESSIBILITY_ENABLED and value not in {"0", "1"}:
            raise ProbeStop(f"{key} secure setting value is ambiguous")
    return {
        "key": key,
        "raw": value,
        "present": present,
        "value": parsed,
        "stdout_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _configured_services(setting: Mapping[str, Any]) -> list[str]:
    if not setting.get("present"):
        return []
    value = setting.get("value")
    if not isinstance(value, str):
        raise ProbeStop("enabled accessibility services value is ambiguous")
    services = [part.strip() for part in value.split(":") if part.strip()]
    if len(services) != len(set(services)):
        raise ProbeStop("enabled accessibility services contain duplicates")
    if any("/" not in service for service in services):
        raise ProbeStop("enabled accessibility services contain an invalid component")
    return services


def _forwarder_identity(run: _Run) -> dict[str, Any]:
    result = run.call(
        "forwarder_identity",
        "shell",
        "dumpsys",
        "package",
        FORWARDER_PACKAGE,
    )
    raw = _raw(getattr(result, "stdout", b""))
    text = _text(raw)
    if getattr(result, "returncode", 1) != 0 or not text.strip():
        raise ProbeStop("forwarder identity read failed")
    if text.count(f"Package [{FORWARDER_PACKAGE}]") != 1:
        raise ProbeStop("forwarder identity is missing or ambiguous")
    short_component = f"{FORWARDER_PACKAGE}/.{FORWARDER_SERVICE_CLASS.rsplit('.', 1)[-1]}"
    if FORWARDER_COMPONENT not in text and short_component not in text:
        raise ProbeStop("known forwarder service component is not installed")
    version_name, version_code = _version(text)
    if version_code is None:
        raise ProbeStop("forwarder package version is unavailable")
    return {
        "package": FORWARDER_PACKAGE,
        "component": FORWARDER_COMPONENT,
        "version_name": version_name,
        "version_code": version_code,
        "service_declared": True,
        "dumpsys_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _snapshot_auxiliary_state(run: _Run) -> dict[str, Any]:
    settings = {
        SECURE_ENABLED_SERVICES: _read_secure_setting(
            run, "snapshot_enabled_accessibility_services", SECURE_ENABLED_SERVICES
        ),
        SECURE_ACCESSIBILITY_ENABLED: _read_secure_setting(
            run, "snapshot_accessibility_enabled", SECURE_ACCESSIBILITY_ENABLED
        ),
    }
    configured = _configured_services(settings[SECURE_ENABLED_SERVICES])
    return {
        "mode": ACQUISITION_MODE,
        "settings": settings,
        "configured_services": configured,
        "forwarder_identity": _forwarder_identity(run),
    }


def _known_forwarder_configured(services: Sequence[str]) -> bool:
    short_component = f"{FORWARDER_PACKAGE}/.{FORWARDER_SERVICE_CLASS.rsplit('.', 1)[-1]}"
    return len(services) == 1 and services[0] in {FORWARDER_COMPONENT, short_component}


def _suspend_auxiliary_state(run: _Run) -> dict[str, Any]:
    run.call(
        "suspend_enabled_accessibility_services",
        "shell",
        "settings",
        "delete",
        "secure",
        SECURE_ENABLED_SERVICES,
    )
    run.call(
        "suspend_accessibility_enabled",
        "shell",
        "settings",
        "put",
        "secure",
        SECURE_ACCESSIBILITY_ENABLED,
        "0",
    )
    run.call(
        "suspend_forwarder_force_stop",
        "shell",
        "am",
        "force-stop",
        FORWARDER_PACKAGE,
    )
    services = _read_secure_setting(
        run,
        "verify_suspended_enabled_accessibility_services",
        SECURE_ENABLED_SERVICES,
    )
    enabled = _read_secure_setting(
        run,
        "verify_suspended_accessibility_enabled",
        SECURE_ACCESSIBILITY_ENABLED,
    )
    if _configured_services(services) or enabled.get("value") != "0":
        raise ProbeStop("forwarder suspension readback is ambiguous")
    return {
        "status": "suspended",
        "settings_readback": {
            SECURE_ENABLED_SERVICES: services,
            SECURE_ACCESSIBILITY_ENABLED: enabled,
        },
    }


def _restore_setting_write(run: _Run, setting: Mapping[str, Any]) -> None:
    key = str(setting["key"])
    if setting.get("present"):
        value = str(setting["value"])
        # Keep an explicitly empty value distinct from an unset key.  Quoting
        # the empty value survives adb shell's argument handling.
        value_arg = '""' if value == "" else value
        run.call("restore_put_" + key, "shell", "settings", "put", "secure", key, value_arg)
    else:
        run.call("restore_delete_" + key, "shell", "settings", "delete", "secure", key)


def _restore_setting(run: _Run, setting: Mapping[str, Any]) -> dict[str, Any]:
    _restore_setting_write(run, setting)
    key = str(setting["key"])
    return _read_secure_setting(run, "restore_read_" + key, key)


def _restore_auxiliary_state(run: _Run, state: Mapping[str, Any]) -> dict[str, Any]:
    settings = state.get("settings")
    if not isinstance(settings, Mapping):
        raise ProbeStop("saved auxiliary settings are unavailable")
    errors: list[str] = []
    for key in (SECURE_ENABLED_SERVICES, SECURE_ACCESSIBILITY_ENABLED):
        try:
            _restore_setting_write(run, settings[key])
        except BaseException as exc:
            errors.append(f"{key} write: {type(exc).__name__}: {exc}")
    readback: dict[str, Any] = {}
    for key in (SECURE_ENABLED_SERVICES, SECURE_ACCESSIBILITY_ENABLED):
        try:
            readback[key] = _read_secure_setting(run, "restore_read_" + key, key)
        except BaseException as exc:
            errors.append(f"{key} readback: {type(exc).__name__}: {exc}")
    for key, expected in settings.items():
        actual = readback.get(key)
        if not isinstance(expected, Mapping) or actual is None:
            errors.append(f"{key} readback is incomplete")
        elif (
            actual.get("present") != expected.get("present")
            or actual.get("value") != expected.get("value")
        ):
            errors.append(f"auxiliary restoration mismatch: {key}")
    if errors:
        raise ProbeStop("; ".join(errors))
    return {"status": "restored", "settings_readback": readback}


def _hierarchy_fragment(raw: bytes, stem: str) -> tuple[str, Any]:
    xml = _text(raw)
    start, end = xml.find("<hierarchy"), xml.rfind("</hierarchy>")
    if start < 0 or end < start:
        raise ProbeStop(f"{stem} XML capture is truncated")
    end += len("</hierarchy>")
    fragment = xml[start:end]
    try:
        root = ElementTree.fromstring(fragment)
    except ElementTree.ParseError as exc:
        raise ProbeStop(f"{stem} XML capture is malformed") from exc
    if root.tag != "hierarchy":
        raise ProbeStop(f"{stem} XML root is not a hierarchy")
    return fragment, root


def _capture_xml(run: _Run, stem: str, phase: str) -> tuple[bytes, str, Any]:
    result = run.call(
        f"{stem}_ax_{phase}",
        "exec-out",
        "uiautomator",
        "dump",
        "--compressed",
        "/dev/tty",
    )
    raw = _raw(getattr(result, "stdout", b""))
    fragment, root = _hierarchy_fragment(raw, f"{stem}_{phase}")
    xml_dir = run.out / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    (xml_dir / f"{stem}_{phase}.xml").write_bytes(raw)
    return raw, fragment, root


def _normalize_hierarchy(root: Any) -> str:
    return ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)


def _capture_coherent(
    run: _Run,
    stem: str,
    activity: str,
    *,
    expected_package: str | None = PACKAGE,
) -> tuple[str, dict[str, Any]]:
    """Capture AX, PNG, AX and require the foreground to stay unchanged."""
    activity_package = _package(activity)
    if not activity_package:
        raise ProbeStop(f"{stem} foreground activity is unavailable")
    if expected_package is not None and activity_package != expected_package:
        raise ProbeStop(f"{stem} foreground package is not Markor")

    raw_before, _before_fragment, before_root = _capture_xml(run, stem, "before")
    before_normalized = _normalize_hierarchy(before_root)
    screenshot_result = run.call(
        f"{stem}_screenshot",
        "exec-out",
        "screencap",
        "-p",
    )
    screenshot = _raw(getattr(screenshot_result, "stdout", b""))
    if not screenshot.startswith(PNG_MAGIC):
        raise ProbeStop(f"{stem} screenshot is not a PNG")
    screenshot_dir = run.out / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    (screenshot_dir / f"{stem}.png").write_bytes(screenshot)

    raw_after, _after_fragment, after_root = _capture_xml(run, stem, "after")
    after_normalized = _normalize_hierarchy(after_root)
    if before_normalized != after_normalized:
        raise ProbeStop(f"{stem} hierarchy changed during capture")

    focus_result = run.call(f"{stem}_focus_after", "shell", "dumpsys", "window")
    focus_text = _text(getattr(focus_result, "stdout", ""))
    activity_after = _activity(focus_text)
    if expected_package is not None and "Application Error:" in focus_text:
        raise ProbeStop(f"{stem} foreground changed to an Android crash overlay")
    if activity_after != activity:
        raise ProbeStop(f"{stem} foreground changed during capture")
    for root in (before_root, after_root):
        xml_package = root.attrib.get("package")
        if xml_package and xml_package != activity_package:
            raise ProbeStop(f"{stem} XML package disagrees with foreground")

    # Keep the historical single-file path as the after-capture view while
    # retaining both raw AX captures for temporal review.
    (run.out / "xml" / f"{stem}.xml").write_bytes(raw_after)
    normalized_hash = hashlib.sha256(after_normalized.encode()).hexdigest()
    return _text(raw_after), {
        "activity": activity,
        "activity_after": activity_after,
        "package": activity_package,
        "xml_package": after_root.attrib.get("package"),
        "xml_sha256": hashlib.sha256(raw_after).hexdigest(),
        "xml_before_sha256": hashlib.sha256(raw_before).hexdigest(),
        "xml_after_sha256": hashlib.sha256(raw_after).hexdigest(),
        "xml_normalized_before_sha256": hashlib.sha256(
            before_normalized.encode()
        ).hexdigest(),
        "xml_normalized_sha256": normalized_hash,
        "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
    }


def _relaunch_markor(run: _Run, component: str) -> tuple[str, str, dict[str, Any]]:
    """Force-stop and relaunch Markor exactly once to refresh its file list."""
    run.call("relaunch_force_stop", "shell", "am", "force-stop", PACKAGE)
    run.call("relaunch_markor", "shell", "am", "start", "-n", component)
    focus = run.call("relaunched_activity", "shell", "dumpsys", "window")
    activity = _activity(_text(getattr(focus, "stdout", "")))
    xml, capture = _capture_coherent(
        run, "after_relaunch", activity, expected_package=PACKAGE
    )
    if not _browser(xml, activity):
        raise ProbeStop("relaunched UI is not a recognizable Markor file browser")
    return activity, xml, capture


def run(
    out: Path | str = DEFAULT_OUT,
    *,
    adb: Any = None,
    host_guard: Any = None,
    ledger_path: Path | str | None = None,
) -> dict[str, Any]:
    out, spec = Path(out).resolve(), load_spec(out)
    if any((out / name).exists() for name in (CLAIM_NAME, SUMMARY_NAME, "run_identity.json")):
        raise ProbeStop("probe output already has run state; rerun is refused")
    ledger = Path(ledger_path or spec["shared_ledger"]).resolve()
    preflight = ledger_snapshot(ledger)
    if not preflight.get("exists") or not preflight.get("ready"):
        raise ProbeStop("shared ledger cannot be read before probe")

    _claim(out, spec)
    guard = host_guard if host_guard is not None else explore_budget.read_host_state
    run_state, created = _Run(out, adb or Adb(), guard), []
    status, error, before, after = "running", None, None, None
    observations: dict[str, Any] = {}
    extraction: dict[str, Any] = {}
    cleanup: list[dict[str, Any]] = []
    auxiliary_state: dict[str, Any] | None = None
    auxiliary_restore_required = False
    current_xml = ""
    current_capture: dict[str, Any] = {}

    try:
        with explore_budget.exclusive_run(
            identity_path=out / "run_identity.json", mode=VERSION
        ):
            try:
                before = ledger_snapshot(ledger)
                if not before.get("exists") or not before.get("ready"):
                    raise ProbeStop("shared ledger cannot be read under lock")
                _host(guard)
                if getattr(run_state.adb, "serial", SERIAL) != spec["expected"]["serial"]:
                    raise ProbeStop("adb serial does not match frozen expectation")
                auxiliary_state = _snapshot_auxiliary_state(run_state)
                observations["auxiliary"] = {
                    "mode": ACQUISITION_MODE,
                    "initial": auxiliary_state,
                }
                configured_services = auxiliary_state["configured_services"]
                if configured_services and not _known_forwarder_configured(
                    configured_services
                ):
                    raise ProbeStop(
                        "foreign accessibility service configured; state left unchanged"
                    )
                if _known_forwarder_configured(configured_services):
                    # Set this before the first write so a partial suspension
                    # is always followed by a restoration attempt.
                    auxiliary_restore_required = True
                    observations["auxiliary"]["suspended"] = _suspend_auxiliary_state(
                        run_state
                    )
                state = run_state.call("device_state", "get-state")
                if _text(getattr(state, "stdout", "")).strip() != "device":
                    raise ProbeStop("unexpected adb device state")
                package = run_state.call(
                    "package_version", "shell", "dumpsys", "package", PACKAGE
                )
                if _version(_text(getattr(package, "stdout", ""))) != (
                    VERSION_NAME,
                    VERSION_CODE,
                ):
                    raise ProbeStop("Markor package version does not match frozen expectation")

                focus = run_state.call("initial_activity", "shell", "dumpsys", "window")
                initial_activity = _activity(_text(getattr(focus, "stdout", "")))
                initial_xml, initial_capture = _capture_coherent(
                    run_state,
                    "initial",
                    initial_activity,
                    expected_package=None,
                )
                observations["initial"] = initial_capture

                resolved = run_state.call(
                    "resolve_launcher",
                    "shell",
                    "cmd",
                    "package",
                    "resolve-activity",
                    "--brief",
                    "-a",
                    "android.intent.action.MAIN",
                    "-c",
                    "android.intent.category.LAUNCHER",
                    PACKAGE,
                )
                component = _component(_text(getattr(resolved, "stdout", "")))
                run_state.call("open_markor", "shell", "am", "start", "-n", component)
                focus = run_state.call("opened_activity", "shell", "dumpsys", "window")
                opened_activity = _activity(_text(getattr(focus, "stdout", "")))
                opened_xml, opened_capture = _capture_coherent(
                    run_state,
                    "after_open",
                    opened_activity,
                    expected_package=PACKAGE,
                )
                observations["after_open"] = opened_capture
                if not _browser(opened_xml, opened_activity):
                    raise ProbeStop("opened UI is not a recognizable Markor file browser")

                for name in spec["filenames"]:
                    _absent(run_state, spec["targets"][name])
                for name in spec["filenames"]:
                    created.append(name)
                    run_state.call(
                        "push_" + name,
                        "push",
                        spec["fixtures"][name]["path"],
                        spec["targets"][name],
                    )

                observations["relaunches"] = []
                if RELAUNCH_LIMIT != 1:
                    raise ProbeStop("v2 relaunch bound changed")
                current_activity, current_xml, current_capture = _relaunch_markor(
                    run_state, component
                )
                observations["relaunches"].append(
                    {"index": 1, "activity": current_activity, **current_capture}
                )
                extraction = extract_rows(
                    current_xml, spec["basename"], spec["filenames"]
                )

                swipes = 0
                observations["swipes"] = []
                while extraction["visible_row_count"] < 4 and swipes < MAX_SWIPES:
                    focus = run_state.call(
                        "swipe_focus_" + str(swipes + 1), "shell", "dumpsys", "window"
                    )
                    swipe_activity = _activity(_text(getattr(focus, "stdout", "")))
                    if not _browser(current_xml, swipe_activity):
                        raise ProbeStop("pre-swipe foreground is not Markor file browser")
                    size = run_state.call(
                        "wm_size_" + str(swipes + 1), "shell", "wm", "size"
                    )
                    width, height = _size(_text(getattr(size, "stdout", "")))
                    before_xml, before_capture = _capture_coherent(
                        run_state,
                        f"before_swipe_{swipes + 1:02d}",
                        swipe_activity,
                        expected_package=PACKAGE,
                    )
                    if not _browser(before_xml, swipe_activity):
                        raise ProbeStop("pre-swipe UI is not a recognizable file browser")
                    run_state.call(
                        f"scroll_to_top_{swipes + 1:02d}",
                        "shell",
                        "input",
                        "swipe",
                        str(width // 2),
                        str(int(height * 0.2)),
                        str(width // 2),
                        str(int(height * 0.8)),
                        "300",
                    )
                    after_focus = run_state.call(
                        "after_swipe_focus_" + str(swipes + 1),
                        "shell",
                        "dumpsys",
                        "window",
                    )
                    after_activity = _activity(
                        _text(getattr(after_focus, "stdout", ""))
                    )
                    current_xml, after_capture = _capture_coherent(
                        run_state,
                        f"after_swipe_{swipes + 1:02d}",
                        after_activity,
                        expected_package=PACKAGE,
                    )
                    if not _browser(current_xml, after_activity):
                        raise ProbeStop("post-swipe UI is not a recognizable file browser")
                    extraction = extract_rows(
                        current_xml, spec["basename"], spec["filenames"]
                    )
                    observations["swipes"].append(
                        {
                            "index": swipes + 1,
                            "activity_before": swipe_activity,
                            "activity_after": after_activity,
                            "before": before_capture,
                            "after": after_capture,
                        }
                    )
                    current_capture = after_capture
                    swipes += 1
                extraction["swipes"] = swipes

                focus = run_state.call("current_activity", "shell", "dumpsys", "window")
                current_activity = _activity(_text(getattr(focus, "stdout", "")))
                observations["current"] = {
                    **current_capture,
                    "activity": current_activity,
                    "package": _package(current_activity),
                }
                if not current_activity.startswith(PACKAGE + "/"):
                    raise ProbeStop("current UI is not in Markor")
                if not _browser(current_xml, current_activity):
                    raise ProbeStop("current UI is not a Markor file browser")
                status = "observed"
            except BaseException as exc:
                status, error = "stopped", f"{type(exc).__name__}: {exc}"
                raise
            finally:
                try:
                    cleanup = v1._cleanup(run_state, spec, created) if created else []
                except BaseException as exc:
                    cleanup = [
                        {"status": "cleanup_failed", "reason": f"{type(exc).__name__}: {exc}"}
                    ]
                restoration: dict[str, Any]
                try:
                    if auxiliary_restore_required and auxiliary_state is not None:
                        restoration = _restore_auxiliary_state(
                            run_state, auxiliary_state
                        )
                    elif auxiliary_state is not None:
                        restoration = {
                            "status": "unchanged",
                            "reason": "known helper was not configured",
                        }
                    else:
                        restoration = {
                            "status": "not_needed",
                            "reason": "auxiliary state was not captured",
                        }
                except BaseException as exc:
                    restoration = {
                        "status": "failed",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                    status = "stopped"
                    message = f"auxiliary restoration failed: {type(exc).__name__}: {exc}"
                    error = f"{error}; {message}" if error else message
                observations.setdefault("auxiliary", {})["restored"] = restoration
                try:
                    after = ledger_snapshot(ledger)
                except ProbeStop as exc:
                    after = {"path": str(ledger), "ready": False, "reason": str(exc)}
                if before is not None:
                    observations["ledger_comparison"] = {"unchanged": before == after}
                if status == "observed" and observations.get("ledger_comparison", {}).get(
                    "unchanged"
                ) is False:
                    status, error = "stopped", "shared ledger changed during probe"
    except BaseException as exc:
        if error is None:
            status, error = "stopped", f"{type(exc).__name__}: {exc}"
    finally:
        if after is None:
            try:
                after = ledger_snapshot(ledger)
            except ProbeStop as exc:
                after = {"path": str(ledger), "ready": False, "reason": str(exc)}
        _write(out / "observations.json", {"schema": SCHEMA, **observations})
        _write(out / "cleanup.json", {"schema": SCHEMA, "outcomes": cleanup})
        _write(
            out / CLAIM_NAME,
            {
                "schema": SCHEMA,
                "version": VERSION,
                "status": status,
                "spec_sha256": spec["spec_sha256"],
                "pid": os.getpid(),
                "ended_unix": time.time(),
            },
        )
        _write(
            out / SUMMARY_NAME,
            {
                "schema": SCHEMA,
                "version": VERSION,
                "status": status,
                "limitation": spec["limitation"],
                "error": error,
                "ledger_preflight": preflight,
                "ledger_before": before,
                "ledger_after": after,
                "observations": observations,
                "extraction": extraction,
                "cleanup": cleanup,
                "created": created,
            },
        )
    if status != "observed":
        raise ProbeStop(error or "probe stopped")
    return json.loads((out / SUMMARY_NAME).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--suffix")
    args = parser.parse_args(argv)
    try:
        value = prepare(args.out, suffix=args.suffix) if args.prepare else run(args.out)
        print(
            json.dumps(
                {
                    "status": value.get("status", "prepared"),
                    "spec_sha256": value.get("spec_sha256"),
                    "exact_command": value.get("cli", {}).get("command"),
                },
                sort_keys=True,
            )
        )
        return 0
    except ProbeStop as exc:
        print(f"STOPPED: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

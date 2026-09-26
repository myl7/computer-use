"""Bounded zero-model Markor filename-label characterization.
Privileged setup only: four owned files, AX captures, no agent/task claim.
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, shlex, sqlite3, subprocess, time, uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from . import selective_explore_budget as explore_budget
from .budget_client import BudgetStop
VERSION, SCHEMA = "markor_label_probe_v1", "selective-markor-label-probe/1"
ROOT = Path(__file__).resolve().parents[2]
ADB_PATH = ROOT / "third-party/android-sdk/platform-tools/adb"
SERIAL, PACKAGE, VERSION_NAME, VERSION_CODE = "emulator-5554", "net.gsantner.markor", "2.10.9", 146
MARKOR_DATA = "/storage/emulated/0/Documents/Markor"
SHARED_LEDGER_PATH = (ROOT / "experimental-results/guiexp_android/revision_20260913/budget.sqlite3").resolve()
SHARED_RUN_LOCK_PATH = (ROOT / "experimental-results/guiexp_android/revision_20260913/run.lock").resolve()
DEFAULT_OUT = (ROOT / "experimental-results/guiexp_android/selective_20260915/markor_label_probe_v1").resolve()
TIMEOUT, MAX_SWIPES = 20.0, 3
SPEC_NAME, CLAIM_NAME, SUMMARY_NAME = "spec.json", "run_claim.json", "summary.json"
class ProbeStop(BudgetStop): pass
def _dump(value: Any) -> str: return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
def _hash(value: Any) -> str: return hashlib.sha256(_dump(value).encode()).hexdigest()
def _fh(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
def _text(value: Any) -> str: return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value if isinstance(value, str) else "")
def _raw(value: Any) -> bytes: return value if isinstance(value, bytes) else str(value or "").encode()
def _names(base: str) -> list[str]: return [base, base + ".txt", base + ".md", base + ".txt.md"]
def exact_command(out: Path | str = DEFAULT_OUT) -> str:
    return ("../.venv-android/bin/python -m guiexp_android.selective_markor_label_probe "
            f"--run --out {shlex.quote(str(Path(out).resolve()))}")
def prepare(out: Path | str = DEFAULT_OUT, *, suffix: str | None = None) -> dict[str, Any]:
    out = Path(out).resolve()
    if (out / SPEC_NAME).is_file():
        return load_spec(out)
    if out.exists() and any(out.iterdir()):
        raise ProbeStop("probe output contains a partial draft")
    suffix = suffix or uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[0-9a-f]{8,64}", suffix):
        raise ProbeStop("probe suffix must be lowercase hexadecimal")
    base, names = "000_cua_probe_" + suffix, _names("000_cua_probe_" + suffix)
    fixture_dir, bodies = out / "fixtures", {name: f"cua_probe_filename={name}\n" for name in names}
    for name, body in bodies.items():
        path = fixture_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    hashes = {name: _fh(fixture_dir / name) for name in names}; source_hash = _fh(Path(__file__).resolve())
    spec: dict[str, Any] = {"schema": SCHEMA, "version": VERSION, "basename": base, "filenames": names,
        "fixtures": {n: {"path": str((fixture_dir / n).resolve()), "sha256": hashes[n]} for n in names},
        "input_sha256": _hash({"bodies": bodies, "sha256": hashes}), "input_hashes": hashes, "source_sha256": source_hash, "source_hashes": {"probe_source": source_hash}, "cleanup_ownership_hashes": hashes, "targets": {n: f"{MARKOR_DATA}/{n}" for n in names},
        "expected": {"serial": SERIAL, "package": PACKAGE, "version_name": VERSION_NAME, "version_code": VERSION_CODE, "markor_data": MARKOR_DATA},
        "shared_ledger": str(SHARED_LEDGER_PATH), "shared_run_lock": str(SHARED_RUN_LOCK_PATH), "execution": {"model_calls": 0, "task_success_claim": False, "max_swipes": MAX_SWIPES, "device_files": names, "cleanup_requires_body_hash": True},
        "limitation": "Privileged benchmark setup for Markor AX filename rendering; not a UI-agent method or task-success experiment.", "cli": {"working_directory": "computer-use", "command": exact_command(out)}}
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
    if value.get("spec_sha256") != _hash({k: v for k, v in value.items() if k != "spec_sha256"}):
        raise ProbeStop("probe spec hash changed")
    if value.get("source_sha256") != _fh(Path(__file__).resolve()):
        raise ProbeStop("probe source changed after preparation")
    if value.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH) or value.get("shared_run_lock") != str(explore_budget.SHARED_RUN_LOCK_PATH):
        raise ProbeStop("probe shared runner lock changed")
    base, names = value.get("basename"), value.get("filenames")
    if not isinstance(base, str) or not base.startswith("000_cua_probe_") or names != _names(base):
        raise ProbeStop("probe filename contract changed")
    fixtures = value.get("fixtures")
    if not isinstance(fixtures, Mapping) or set(fixtures) != set(names):
        raise ProbeStop("probe fixtures are incomplete")
    bodies, body_hashes = {}, {}
    for name in names:
        item, path = fixtures[name], Path(str(fixtures[name].get("path", ""))).resolve()
        if not path.is_file() or _fh(path) != item.get("sha256"):
            raise ProbeStop(f"probe fixture changed: {name}")
        bodies[name] = path.read_text(encoding="utf-8")
        body_hashes[name] = item["sha256"]
        if bodies[name] != f"cua_probe_filename={name}\n": raise ProbeStop(f"probe fixture body changed: {name}")
    if value.get("input_sha256") != _hash({"bodies": bodies, "sha256": body_hashes}) or value.get("input_hashes") != body_hashes or value.get("cleanup_ownership_hashes") != body_hashes:
        raise ProbeStop("probe input hash changed")
    expected = {"serial": SERIAL, "package": PACKAGE, "version_name": VERSION_NAME,
                "version_code": VERSION_CODE, "markor_data": MARKOR_DATA}
    if value.get("expected") != expected:
        raise ProbeStop("probe device or package expectation changed")
    return dict(value)
def ledger_snapshot(path: Path | str = SHARED_LEDGER_PATH) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file():
        return {"path": str(path), "exists": False, "ready": False, "reason": "ledger missing"}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as db:
            settings = dict(db.execute("SELECT key,value FROM settings").fetchall())
            rows = db.execute("SELECT state,reserved_nano,actual_nano FROM calls ORDER BY id").fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise ProbeStop("shared ledger read-only snapshot failed") from exc
    return {"path": str(path), "exists": True, "ready": True, "limit_nano": settings.get("limit_nano"),
            "calls": len(rows), "states": [str(r[0]) for r in rows],
            "reserved_nano": sum(int(r[1] or 0) for r in rows), "actual_nano": sum(int(r[2] or 0) for r in rows)}
def _host(guard: Any) -> Any:
    value = guard() if callable(guard) else guard
    if value is False or value is None or (isinstance(value, Mapping) and
       (not value.get("ready", False) or value.get("normal_full_wake") is False)):
        raise ProbeStop("host is not in normal full wake")
    return value
class Adb:
    def __init__(self, adb_path: Path | str = ADB_PATH, serial: str = SERIAL, timeout: float = TIMEOUT, runner: Any = subprocess.run):
        self.path, self.serial, self.timeout, self.runner = str(adb_path), serial, timeout, runner
    def run(self, *args: str, check: bool = True) -> Any:
        try: result = self.runner([self.path, "-s", self.serial, *map(str, args)], capture_output=True, timeout=self.timeout)
        except Exception as exc: raise ProbeStop("bounded adb command failed") from exc
        if check and getattr(result, "returncode", 1) != 0: raise ProbeStop("adb command returned nonzero")
        return result
class _Run:
    def __init__(self, out: Path, adb: Any, guard: Any):
        self.out, self.adb, self.guard, self.number = out, adb, guard, 0
        (out / "receipts").mkdir(parents=True, exist_ok=True)
    def call(self, label: str, *args: str, check: bool = True) -> Any:
        _host(self.guard); started = time.time(); result = None; error = None
        try:
            result = self.adb.run(*args, check=False)
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.number += 1; stdout = _raw(getattr(result, "stdout", b"")); stderr = _text(getattr(result, "stderr", ""))
        receipt = {"schema": SCHEMA, "label": label, "args": list(args), "started_unix": started, "ended_unix": time.time(),
                   "returncode": getattr(result, "returncode", None), "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                   "stdout_bytes": len(stdout), "stderr": stderr, "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest()}
        if len(stdout) <= 200_000: receipt["stdout"] = _text(stdout)
        if error: receipt["error"] = error
        _write(self.out / "receipts" / f"{self.number:04d}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', label)[:80]}.json", receipt)
        if error or (check and getattr(result, "returncode", 1) != 0):
            raise ProbeStop(f"{label} failed")
        return result
def _capture(run: _Run, stem: str) -> str:
    xr = run.call(stem + "_xml", "exec-out", "uiautomator", "dump", "--compressed", "/dev/tty")
    xml = _raw(getattr(xr, "stdout", b""))
    (run.out / "xml").mkdir(exist_ok=True); (run.out / "xml" / f"{stem}.xml").write_bytes(xml)
    start, end = xml.find(b"<hierarchy"), xml.rfind(b"</hierarchy>")
    if start < 0 or end < start: raise ProbeStop(f"{stem} XML capture is truncated")
    try: ElementTree.fromstring(xml[start:end + len(b"</hierarchy>")])
    except ElementTree.ParseError as exc: raise ProbeStop(f"{stem} XML capture is malformed") from exc
    sr = run.call(stem + "_screenshot", "exec-out", "screencap", "-p"); shot = _raw(getattr(sr, "stdout", b""))
    if not shot: raise ProbeStop(f"{stem} screenshot capture is empty")
    (run.out / "screenshots").mkdir(exist_ok=True); (run.out / "screenshots" / f"{stem}.png").write_bytes(shot)
    return xml.decode("utf-8", "replace")
_ATTR = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*(['\"])(.*?)\2", re.S)
def _nodes(xml: str) -> list[dict[str, Any]]:
    return [{"node_index": i, "raw_attributes": (raw := m.group(1)), "attributes": {a.group(1): html.unescape(a.group(3)) for a in _ATTR.finditer(raw)}} for i, m in enumerate(re.finditer(r"<node\b([^>]*)/?>", xml, re.S))]
def _filename_counts(value: str, filenames: Sequence[str]) -> dict[str, int]:
    token = r"A-Za-z0-9_.-"
    return {name: len(re.findall(rf"(?<![{token}]){re.escape(name)}(?![{token}])", value)) for name in sorted(filenames, key=len, reverse=True)}
def extract_rows(xml: str, basename: str, filenames: Sequence[str]) -> dict[str, Any]:
    rows, counts = [], {name: 0 for name in filenames}
    for node in _nodes(xml):
        attrs = node["attributes"]; text, desc = attrs.get("text", ""), attrs.get("content-desc", "")
        if basename not in text and basename not in desc: continue
        each = _filename_counts(text + "\n" + desc, filenames)
        for name, count in each.items(): counts[name] += count
        rows.append(dict(node, text=text, content_desc=desc, filename_counts=each))
    visible = [name for name in filenames if counts[name] > 0]
    return {"rows": rows, "exact_filename_counts": counts, "visible_row_count": len(rows),
            "visible_exact_filenames": visible, "visibility": "full_exact_names" if len(visible) == len(filenames) else "incomplete_or_collapsed",
            "visible_distinct_labels": len({(r["text"], r["content_desc"]) for r in rows})}
def _browser(xml: str, activity: str) -> bool:
    if not activity.startswith(PACKAGE + "/"): return False
    nodes = _nodes(xml); classes = [n["attributes"].get("class", "") for n in nodes]
    labels = " ".join(n["attributes"].get("text", "") + " " + n["attributes"].get("content-desc", "") for n in nodes)
    return any("RecyclerView" in c or "ListView" in c for c in classes) or MARKOR_DATA in labels
def _component(text: str) -> str:
    values = {v.rstrip("]}") for v in re.findall(rf"{re.escape(PACKAGE)}/[^\s\r\n]+", text)}
    if len(values) != 1: raise ProbeStop("launcher activity resolution is ambiguous")
    return values.pop()
def _activity(text: str) -> str:
    pattern = re.compile(r"\b([A-Za-z_][\w.]*)/([A-Za-z_$.][\w.$]*)")
    lines = text.splitlines()
    for marker in ("mCurrentFocus", "mFocusedApp"):
        for line in lines:
            if marker not in line: continue
            values = pattern.findall(line)
            if values: return "/".join(values[-1])
    return ""
def _package(activity: str) -> str | None: return activity.split("/", 1)[0] if "/" in activity else None
def _version(text: str) -> tuple[str | None, int | None]:
    name, code = re.search(r"versionName=([^\s]+)", text), re.search(r"versionCode=(\d+)", text)
    return (name.group(1) if name else None, int(code.group(1)) if code else None)
def _size(text: str) -> tuple[int, int]:
    if not (m := re.search(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", text)): raise ProbeStop("wm size is unavailable")
    return int(m.group(1)), int(m.group(2))
def _claim(out: Path, spec: Mapping[str, Any]) -> None:
    try:
        fd = os.open(out / CLAIM_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump({"schema": SCHEMA, "version": VERSION, "status": "claimed", "pid": os.getpid(), "spec_sha256": spec["spec_sha256"], "claimed_unix": time.time()}, f); f.write("\n")
    except FileExistsError as exc:
        raise ProbeStop("probe lifetime claim already exists; rerun is refused") from exc
def _absent(run: _Run, path: str) -> None:
    result = run.call("absence_check", "shell", "ls", "-ld", path, check=False)
    if getattr(result, "returncode", 1) == 0: raise ProbeStop(f"probe target already exists: {path}")
    if not re.search(r"No such file|cannot access", _text(getattr(result, "stderr", ""))): raise ProbeStop(f"probe target absence is ambiguous: {path}")
def _cleanup(run: _Run, spec: Mapping[str, Any], created: Sequence[str]) -> list[dict[str, Any]]:
    result = []
    for name in created:
        target, expected = spec["targets"][name], spec["fixtures"][name]["sha256"]
        item = {"filename": name, "target": target, "expected_sha256": expected}
        try: r = run.call("cleanup_hash_" + name, "shell", "sha256sum", target, check=False)
        except ProbeStop as exc: item.update(status="unknown", reason=str(exc)); result.append(item); continue
        words = _text(getattr(r, "stdout", "")).split(); current = words[0] if getattr(r, "returncode", 1) == 0 and words else None
        item["current_sha256"] = current
        if current is None: item.update(status="unknown", reason="ownership hash unavailable")
        elif current != expected: item.update(status="changed", reason="content hash differs; file left in place")
        else:
            try:
                rm = run.call("cleanup_remove_" + name, "shell", "rm", target, check=False)
                item["status"] = "removed" if getattr(rm, "returncode", 1) == 0 else "remove_failed"
            except ProbeStop as exc: item.update(status="remove_failed", reason=str(exc))
        result.append(item)
    return result
def run(out: Path | str = DEFAULT_OUT, *, adb: Any = None, host_guard: Any = None,
        ledger_path: Path | str | None = None) -> dict[str, Any]:
    out, spec = Path(out).resolve(), load_spec(out)
    if any((out / n).exists() for n in (CLAIM_NAME, SUMMARY_NAME, "run_identity.json")): raise ProbeStop("probe output already has run state; rerun is refused")
    ledger = Path(ledger_path or spec["shared_ledger"]).resolve(); preflight = ledger_snapshot(ledger)
    if not preflight.get("exists") or not preflight.get("ready"): raise ProbeStop("shared ledger cannot be read before probe")
    _claim(out, spec); guard = host_guard if host_guard is not None else explore_budget.read_host_state
    run_state, created = _Run(out, adb or Adb(), guard), []
    status, error, before, after, observations, extraction, cleanup = "running", None, None, None, {}, {}, []
    try:
        with explore_budget.exclusive_run(identity_path=out / "run_identity.json", mode=VERSION):
            try:
                before = ledger_snapshot(ledger)
                if not before.get("exists") or not before.get("ready"): raise ProbeStop("shared ledger cannot be read under lock")
                _host(guard)
                if getattr(run_state.adb, "serial", SERIAL) != spec["expected"]["serial"]: raise ProbeStop("adb serial does not match frozen expectation")
                state = run_state.call("device_state", "get-state")
                if _text(getattr(state, "stdout", "")).strip() != "device": raise ProbeStop("unexpected adb device state")
                package = run_state.call("package_version", "shell", "dumpsys", "package", PACKAGE)
                if _version(_text(getattr(package, "stdout", ""))) != (VERSION_NAME, VERSION_CODE): raise ProbeStop("Markor package version does not match frozen expectation")
                focus = run_state.call("initial_activity", "shell", "dumpsys", "window")
                initial_activity = _activity(_text(getattr(focus, "stdout", ""))); initial_xml = _capture(run_state, "initial")
                observations["initial"] = {"package": _package(initial_activity), "activity": initial_activity, "xml_sha256": _hash(initial_xml)}
                resolved = run_state.call("resolve_launcher", "shell", "cmd", "package", "resolve-activity", "--brief", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", PACKAGE)
                component = _component(_text(getattr(resolved, "stdout", ""))); run_state.call("open_markor", "shell", "am", "start", "-n", component)
                focus = run_state.call("opened_activity", "shell", "dumpsys", "window")
                opened_activity = _activity(_text(getattr(focus, "stdout", ""))); opened_xml = _capture(run_state, "after_open")
                observations["after_open"] = {"package": _package(opened_activity), "activity": opened_activity, "xml_sha256": _hash(opened_xml)}
                if not _browser(opened_xml, opened_activity): raise ProbeStop("opened UI is not a recognizable Markor file browser")
                for name in spec["filenames"]: _absent(run_state, spec["targets"][name])
                for name in spec["filenames"]:
                    created.append(name); run_state.call("push_" + name, "push", spec["fixtures"][name]["path"], spec["targets"][name])
                current_xml = _capture(run_state, "after_push"); extraction = extract_rows(current_xml, spec["basename"], spec["filenames"])
                swipes = 0; observations["swipes"] = []
                while extraction["visible_row_count"] < 4 and swipes < MAX_SWIPES:
                    focus = run_state.call("swipe_focus_" + str(swipes + 1), "shell", "dumpsys", "window")
                    swipe_activity = _activity(_text(getattr(focus, "stdout", "")))
                    if not _browser(current_xml, swipe_activity): raise ProbeStop("pre-swipe foreground is not Markor file browser")
                    size = run_state.call("wm_size_" + str(swipes + 1), "shell", "wm", "size")
                    width, height = _size(_text(getattr(size, "stdout", ""))); before_xml = _capture(run_state, f"before_swipe_{swipes + 1:02d}")
                    if not _browser(before_xml, swipe_activity): raise ProbeStop("pre-swipe UI is not a recognizable file browser")
                    run_state.call(f"scroll_to_top_{swipes + 1:02d}", "shell", "input", "swipe", str(width // 2), str(int(height * .2)), str(width // 2), str(int(height * .8)), "300")
                    current_xml = _capture(run_state, f"after_swipe_{swipes + 1:02d}"); extraction = extract_rows(current_xml, spec["basename"], spec["filenames"]); observations["swipes"].append({"index": swipes + 1, "activity": swipe_activity, "before_xml_sha256": _hash(before_xml), "after_xml_sha256": _hash(current_xml)}); swipes += 1
                extraction["swipes"] = swipes
                focus = run_state.call("current_activity", "shell", "dumpsys", "window")
                current_activity = _activity(_text(getattr(focus, "stdout", "")))
                observations["current"] = {"package": _package(current_activity), "activity": current_activity, "xml_sha256": _hash(current_xml)}
                if not current_activity.startswith(PACKAGE + "/"): raise ProbeStop("current UI is not in Markor")
                if not _browser(current_xml, current_activity): raise ProbeStop("current UI is not a Markor file browser")
                status = "observed"
            except BaseException as exc:
                status, error = "stopped", f"{type(exc).__name__}: {exc}"; raise
            finally:
                try: cleanup = _cleanup(run_state, spec, created) if created else []
                except BaseException as exc: cleanup = [{"status": "cleanup_failed", "reason": f"{type(exc).__name__}: {exc}"}]
                try: after = ledger_snapshot(ledger)
                except ProbeStop as exc: after = {"path": str(ledger), "ready": False, "reason": str(exc)}
                if before is not None: observations["ledger_comparison"] = {"unchanged": before == after}
                if status == "observed" and observations.get("ledger_comparison", {}).get("unchanged") is False:
                    status, error = "stopped", "shared ledger changed during probe"
    except BaseException as exc:
        if error is None: status, error = "stopped", f"{type(exc).__name__}: {exc}"
    finally:
        if after is None:
            try: after = ledger_snapshot(ledger)
            except ProbeStop as exc: after = {"path": str(ledger), "ready": False, "reason": str(exc)}
        _write(out / "observations.json", {"schema": SCHEMA, **observations}); _write(out / "cleanup.json", {"schema": SCHEMA, "outcomes": cleanup})
        _write(out / CLAIM_NAME, {"schema": SCHEMA, "version": VERSION, "status": status, "spec_sha256": spec["spec_sha256"], "pid": os.getpid(), "ended_unix": time.time()})
        _write(out / SUMMARY_NAME, {"schema": SCHEMA, "version": VERSION, "status": status, "limitation": spec["limitation"], "error": error, "ledger_preflight": preflight, "ledger_before": before, "ledger_after": after, "observations": observations, "extraction": extraction, "cleanup": cleanup, "created": created})
    if status != "observed": raise ProbeStop(error or "probe stopped")
    return json.loads((out / SUMMARY_NAME).read_text(encoding="utf-8"))
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0]); group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true"); group.add_argument("--run", action="store_true"); parser.add_argument("--out", type=Path, default=DEFAULT_OUT); parser.add_argument("--suffix")
    args = parser.parse_args(argv)
    try:
        value = prepare(args.out, suffix=args.suffix) if args.prepare else run(args.out)
        print(json.dumps({"status": value.get("status", "prepared"), "spec_sha256": value.get("spec_sha256"), "exact_command": value.get("cli", {}).get("command")}, sort_keys=True)); return 0
    except ProbeStop as exc:
        print(f"STOPPED: {type(exc).__name__}: {exc}"); return 2
if __name__ == "__main__": raise SystemExit(main())

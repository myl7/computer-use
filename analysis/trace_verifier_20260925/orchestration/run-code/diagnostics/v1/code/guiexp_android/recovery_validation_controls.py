"""Fixed public-state controls for the recovery diagnostic.

Only goal-extracted parameters and the documented public device API are used.
Content signatures require a complete, strictly decoded UTF-8 read. Neither an
unavailable read nor an initially missing target establishes task success.
Dot-prefixed files and directories are excluded as app-maintained metadata.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from pathlib import PurePosixPath


STORAGE = "/storage/emulated/0"
PUBLIC_ROOTS = frozenset({
    "Alarms", "Audiobooks", "DCIM", "Documents", "Download", "Movies",
    "Music", "Notifications", "Pictures", "Podcasts", "Recordings", "Ringtones",
})
APP_PACKAGES = {"Markor": "net.gsantner.markor", "Files": "com.google.android.documentsui"}
LAUNCHER_PACKAGES = frozenset({
    "com.google.android.apps.nexuslauncher", "com.android.launcher3", "com.android.launcher",
})
SHADE_RESOURCES = frozenset({
    "notification_panel", "notification_stack_scroller", "quick_settings_panel",
    "qs_panel", "qs_frame",
})


class ContractConstructionError(ValueError):
    """The initial public state does not identify a valid task instance."""


class VerificationUnavailable(RuntimeError):
    """Public observations cannot establish the requested effect."""


def _public_path(value):
    if not isinstance(value, str) or not value.startswith("/"):
        raise ContractConstructionError("An absolute public-storage path is required")
    if ".." in value.split("/") or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ContractConstructionError("Invalid public-storage path")
    if any(c in value for c in ";$`|&<>"):
        raise ContractConstructionError("Invalid public-storage path")
    if value == "/sdcard" or value.startswith("/sdcard/"):
        value = STORAGE + value[len("/sdcard"):]
    path = str(PurePosixPath(value))
    if not any(path == f"{STORAGE}/{r}" or path.startswith(f"{STORAGE}/{r}/") for r in PUBLIC_ROOTS):
        raise ContractConstructionError("Path is outside permitted public storage")
    return path


def _filename(value):
    if not isinstance(value, str) or not value or value in {".", ".."} or "/" in value:
        raise ContractConstructionError("An exact filename is required")
    _public_path(f"{STORAGE}/Documents/{value}")
    if value.startswith("."):
        raise ContractConstructionError("Hidden targets are outside the declared inventory")
    return value


def _directory(value):
    if not isinstance(value, str) or not value:
        raise ContractConstructionError("A public directory is required")
    path = _public_path(value if value.startswith("/") else f"{STORAGE}/{value}")
    if any(part.startswith(".") for part in PurePosixPath(path).parts):
        raise ContractConstructionError("Hidden directories are outside the declared inventory")
    return path


class EffectContract:
    """Snapshot a bounded public scope, then check the complete expected delta.

    ``check`` returns False immediately when the target still exists. A True
    result requires the complete expected inventory. Read failures, malformed
    listings, binary content and exceeded bounds raise
    ``VerificationUnavailable`` and leave serializable unknown progress.
    """

    def __init__(self, family, params, device, *, max_depth=4, max_entries=256,
                 max_file_bytes=1_048_576, max_total_bytes=8_388_608):
        if not isinstance(params, Mapping):
            raise ContractConstructionError("Parameters must come from goal extraction")
        for name, value in {"max_depth": max_depth, "max_entries": max_entries,
                            "max_file_bytes": max_file_bytes, "max_total_bytes": max_total_bytes}.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if max_file_bytes > 1_048_576:
            raise ValueError("max_file_bytes exceeds the public device read limit")
        self._device = device
        self.family = family
        self.bounds = {"max_depth": max_depth, "max_entries": max_entries,
                       "max_file_bytes": max_file_bytes, "max_total_bytes": max_total_bytes}
        self._last = {"state": "not_checked", "verified": None, "checks": 0}
        name = _filename(params.get("file_name"))
        if family == "MarkorDeleteNote":
            self.roots = (f"{STORAGE}/Documents",)
        elif family == "FilesMoveFile":
            source = _directory(params.get("source_folder"))
            destination = _directory(params.get("destination_folder"))
            if source == destination:
                raise ContractConstructionError("Move source and destination are identical")
            candidates = sorted({source, destination}, key=lambda p: (len(p), p))
            self.roots = tuple(p for p in candidates if not any(
                p.startswith(other + "/") for other in candidates if other != p))
        else:
            raise ContractConstructionError("Unsupported task family")
        before = self._snapshot()
        if family == "MarkorDeleteNote":
            exact = [p for p in before if PurePosixPath(p).name == name]
            candidates = exact or [p for p in before if PurePosixPath(p).stem == name]
            if len(candidates) != 1:
                raise ContractConstructionError("Initial delete target is missing or ambiguous")
            self.target = candidates[0]
            self.destination = None
        else:
            self.target = str(PurePosixPath(source) / name)
            self.destination = str(PurePosixPath(destination) / name)
            if self.target not in before:
                raise ContractConstructionError("Initial move source file is missing")
            if self.destination in before:
                raise ContractConstructionError("Move would overwrite an existing destination file")
        self._expected = dict(before)
        signature = self._expected.pop(self.target)
        if self.destination is not None:
            self._expected[self.destination] = signature
        self._effect = {"absent": self.target, "present": self.destination,
                        "content_signature": signature, "unrelated_files": len(before) - 1}

    def _snapshot(self):
        files, seen, entries, total_bytes = {}, set(), 0, 0

        def walk(directory, depth):
            nonlocal entries, total_bytes
            if directory in seen or depth > self.bounds["max_depth"]:
                raise VerificationUnavailable("Directory recursion limit or cycle encountered")
            seen.add(directory)
            listing = self._device.list_files(directory)
            if not isinstance(listing, list):
                raise VerificationUnavailable("Public directory listing is unavailable")
            children = set()
            for item in listing:
                entries += 1
                if entries > self.bounds["max_entries"]:
                    raise VerificationUnavailable("Public snapshot entry limit exceeded")
                if not isinstance(item, Mapping) or type(item.get("is_dir")) is not bool:
                    raise VerificationUnavailable("Public listing lacks file/directory evidence")
                child = _public_path(item.get("path"))
                if (str(PurePosixPath(child).parent) != directory
                        or item.get("name") != PurePosixPath(child).name or child in children):
                    raise VerificationUnavailable("Public listing has an invalid or duplicate child")
                children.add(child)
                if item["name"].startswith("."):
                    continue
                if item["is_dir"]:
                    walk(child, depth + 1)
                else:
                    content = self._device.read_file(child, max_bytes=self.bounds["max_file_bytes"])
                    if not isinstance(content, str):
                        raise VerificationUnavailable("Public file did not return UTF-8 text")
                    encoded = content.encode("utf-8", errors="strict")
                    total_bytes += len(encoded)
                    if len(encoded) > self.bounds["max_file_bytes"] or total_bytes > self.bounds["max_total_bytes"]:
                        raise VerificationUnavailable("Public snapshot byte limit exceeded")
                    files[child] = {"bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}

        try:
            for root in self.roots:
                walk(root, 0)
        except VerificationUnavailable:
            raise
        except Exception as exc:
            raise VerificationUnavailable(f"Public snapshot failed: {type(exc).__name__}") from exc
        return files

    def check(self):
        self._last["checks"] += 1
        try:
            try:
                target_exists = self._device.exists(self.target)
            except Exception as exc:
                raise VerificationUnavailable(
                    f"Public target existence check failed: {type(exc).__name__}"
                ) from exc
            if type(target_exists) is not bool:
                raise VerificationUnavailable("Public target existence check did not return a bool")
            if target_exists:
                self._last.update(state="checked", verified=False, reason="target_still_present")
                self._last.pop("differences", None)
                return False
            actual = self._snapshot()
        except VerificationUnavailable as exc:
            self._last.update(state="unknown", verified=None, reason=str(exc))
            self._last.pop("differences", None)
            raise
        differences = {
            "missing": sorted(set(self._expected) - set(actual)),
            "unexpected": sorted(set(actual) - set(self._expected)),
            "changed": sorted(p for p in set(actual) & set(self._expected) if actual[p] != self._expected[p]),
        }
        verified = not any(differences.values())
        self._last.update(state="checked", verified=verified, differences=differences)
        self._last.pop("reason", None)
        return verified

    def progress(self):
        return copy.deepcopy({"family": self.family, "scope": list(self.roots),
                              "inventory_excludes": "dot-prefixed files and directories, including app metadata",
                              "bounds": self.bounds, "expected_effects": self._effect, **self._last})


def _nodes(observation):
    pending = [observation.get("action_elements", []), observation.get("ax_forest", {})]
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        if count > 20_000:
            raise VerificationUnavailable("AX traversal limit exceeded")
        if isinstance(value, Mapping):
            yield value
            pending.extend(v for v in value.values() if isinstance(v, (Mapping, list)))
        elif isinstance(value, list):
            pending.extend(value)


def _expanded_shade(observation):
    for node in _nodes(observation):
        flags = [node[k] for k in ("is_visible", "is_visible_to_user", "visible") if k in node]
        if True not in flags or False in flags:
            continue
        for key in ("resource_name", "resource_id", "view_id_resource_name"):
            resource = node.get(key)
            if not isinstance(resource, str) or not resource.startswith("com.android.systemui:id/"):
                continue
            if resource.rsplit("/", 1)[-1] in SHADE_RESOURCES:
                return True
    return False


def _app_spec(expected_app):
    if isinstance(expected_app, str) and expected_app in APP_PACKAGES:
        return expected_app, APP_PACKAGES[expected_app]
    if isinstance(expected_app, Mapping):
        name, package = expected_app.get("app_name"), expected_app.get("package_name")
        if isinstance(name, str) and name.strip() and isinstance(package, str) and package.strip() and "/" not in package:
            return name, package
    raise ValueError("expected_app needs a known app name or app_name/package_name mapping")


def normalize_ui(device, expected_app):
    """Close a visible SystemUI shade and return from the launcher, <=2 actions.

    Status-bar icons and notification text alone never imply an expanded shade.
    The caller supplies an unwrapped device so these observations cannot recurse.
    """
    app_name, package = _app_spec(expected_app)
    actions = []
    result = {"normalized": False, "actions": actions, "expected_app": app_name,
              "expected_package": package, "shade_detected": False}
    try:
        for attempt in range(3):
            observation = device.observe()
            if not isinstance(observation, Mapping):
                raise VerificationUnavailable("Current UI observation is unavailable")
            activity = str(observation.get("activity") or observation.get("url") or "")
            shade = _expanded_shade(observation)
            result.update(final_activity=activity, remaining_shade=shade)
            result["shade_detected"] |= shade
            if not shade and activity.split("/", 1)[0] == package:
                result.update(normalized=True, reason="expected_app_visible")
                break
            if attempt == 2:
                result["reason"] = "normalization_action_limit"
                break
            if shade:
                actions.append({"action_type": "navigate_back"})
                device.navigate_back()
            elif activity.split("/", 1)[0] in LAUNCHER_PACKAGES:
                actions.append({"action_type": "open_app", "app_name": app_name})
                device.open_app(app_name)
            elif activity:
                result["reason"] = "other_activity_not_normalized"
                break
            else:
                result["reason"] = "activity_unknown"
                break
    except Exception as exc:
        result.update(normalized=False, reason="normalization_unavailable", error=type(exc).__name__)
    result["action_count"] = len(actions)
    return result

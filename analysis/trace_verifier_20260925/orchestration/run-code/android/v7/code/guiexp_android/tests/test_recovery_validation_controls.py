import copy
import json
from pathlib import PurePosixPath

import pytest

from guiexp_android.recovery_validation_controls import (
    ContractConstructionError, EffectContract, VerificationUnavailable, normalize_ui,
)


ROOT = "/storage/emulated/0"
DOCS = ROOT + "/Documents"


class PublicFiles:
    def __init__(self, files, directories=()):
        self.files = dict(files)
        self.directories = set(directories)
        for path in files:
            self.directories.update(str(p) for p in PurePosixPath(path).parents)
        self.fail = set()
        self.calls = []

    def list_files(self, path):
        self.calls.append(("list", path))
        if path in self.fail:
            raise OSError("read unavailable")
        if path not in self.directories:
            raise FileNotFoundError(path)
        paths = {p for p in self.files.keys() | self.directories if str(PurePosixPath(p).parent) == path}
        return [{"name": PurePosixPath(p).name, "path": p, "is_dir": p in self.directories} for p in sorted(paths)]

    def read_file(self, path, max_bytes):
        self.calls.append(("read", path))
        if path in self.fail:
            raise OSError("read unavailable")
        value = self.files[path]
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def exists(self, path):
        self.calls.append(("exists", path))
        if path in self.fail:
            raise OSError("existence query unavailable")
        return path in self.files or path in self.directories

    def __getattr__(self, name):
        pytest.fail(f"Effect contract accessed an undocumented capability: {name}")


def delete_device():
    return PublicFiles({DOCS + "/markor/target.md": "target contents", DOCS + "/other.txt": "keep"})


def test_delete_exact_name_and_unique_stem_use_only_public_reads():
    for name in ("target.md", "target"):
        device = delete_device()
        contract = EffectContract("MarkorDeleteNote", {"file_name": name}, device)
        assert contract.check() is False
        del device.files[DOCS + "/markor/target.md"]
        assert contract.check() is True
        progress = contract.progress()
        assert progress["state"] == "checked" and progress["verified"] is True
        assert progress["expected_effects"]["absent"] == DOCS + "/markor/target.md"
        assert all(kind in {"list", "read", "exists"} for kind, _ in device.calls)
        assert "target contents" not in json.dumps(progress)


def test_exact_name_takes_precedence_over_other_extension():
    device = PublicFiles({DOCS + "/name": "a", DOCS + "/name.md": "b"})
    contract = EffectContract("MarkorDeleteNote", {"file_name": "name"}, device)
    del device.files[DOCS + "/name"]
    assert contract.check() is True


@pytest.mark.parametrize("files", [{}, {DOCS + "/a/name.md": "a", DOCS + "/b/name.md": "b"},
                                 {DOCS + "/name.md": "a", DOCS + "/name.txt": "b"}])
def test_missing_or_ambiguous_initial_delete_is_not_success(files):
    with pytest.raises(ContractConstructionError, match="missing or ambiguous"):
        EffectContract("MarkorDeleteNote", {"file_name": "name"}, PublicFiles(files, [DOCS]))


@pytest.mark.parametrize("change", ["delete", "change", "add"])
def test_delete_rejects_unrelated_file_changes(change):
    device = delete_device()
    contract = EffectContract("MarkorDeleteNote", {"file_name": "target.md"}, device)
    del device.files[DOCS + "/markor/target.md"]
    if change == "delete":
        del device.files[DOCS + "/other.txt"]
    elif change == "change":
        device.files[DOCS + "/other.txt"] = "corrupted"
    else:
        device.files[DOCS + "/extra.txt"] = "unexpected"
    assert contract.check() is False


def test_read_failure_after_deletion_is_unknown_not_success():
    device = delete_device()
    contract = EffectContract("MarkorDeleteNote", {"file_name": "target"}, device)
    del device.files[DOCS + "/markor/target.md"]
    device.fail.add(DOCS + "/other.txt")
    with pytest.raises(VerificationUnavailable):
        contract.check()
    assert contract.progress()["state"] == "unknown"
    assert contract.progress()["verified"] is None
    device.fail.clear()
    assert contract.check() is True
    assert "reason" not in contract.progress()


def test_existing_target_short_circuits_without_inventory_or_content_reads():
    device = delete_device()
    contract = EffectContract("MarkorDeleteNote", {"file_name": "target"}, device)
    device.calls.clear()
    device.fail.add(DOCS + "/other.txt")
    assert contract.check() is False
    assert device.calls == [("exists", DOCS + "/markor/target.md")]
    assert contract.progress()["state"] == "checked"
    assert contract.progress()["reason"] == "target_still_present"


def test_existence_query_error_is_unknown_without_inventory_fallback():
    device = delete_device()
    contract = EffectContract("MarkorDeleteNote", {"file_name": "target"}, device)
    target = DOCS + "/markor/target.md"
    del device.files[target]
    device.calls.clear()
    device.fail.add(target)
    with pytest.raises(VerificationUnavailable, match="existence check failed"):
        contract.check()
    assert device.calls == [("exists", target)]
    assert contract.progress()["state"] == "unknown"
    assert contract.progress()["verified"] is None


def test_unavailable_existence_value_cannot_establish_target_absence():
    device = delete_device()
    contract = EffectContract("MarkorDeleteNote", {"file_name": "target"}, device)
    device.exists = lambda path: None
    with pytest.raises(VerificationUnavailable, match="did not return a bool"):
        contract.check()
    assert contract.progress()["state"] == "unknown"
    assert contract.progress()["verified"] is None


def test_hidden_metadata_is_explicitly_excluded_but_visible_files_are_checked():
    device = delete_device()
    device.files[DOCS + "/markor/.app/cache"] = b"\xff"
    device.directories.add(DOCS + "/markor/.app")
    device.files[DOCS + "/.index"] = None
    contract = EffectContract("MarkorDeleteNote", {"file_name": "target"}, device)
    del device.files[DOCS + "/markor/target.md"]
    del device.files[DOCS + "/.index"]
    device.files[DOCS + "/markor/.app/cache"] = "changed metadata"
    assert contract.check() is True
    assert "dot-prefixed" in contract.progress()["inventory_excludes"]
    assert not any(kind == "read" and "/." in path for kind, path in device.calls)
    device.files[DOCS + "/other.txt"] = "changed user file"
    assert contract.check() is False


def test_hidden_requested_target_is_an_explicit_construction_failure():
    with pytest.raises(ContractConstructionError, match="Hidden targets"):
        EffectContract("MarkorDeleteNote", {"file_name": ".target"}, PublicFiles({DOCS + "/.target": "x"}))


@pytest.mark.parametrize("bad_read", [None, b"\xff"])
def test_initial_unreadable_content_is_explicit(bad_read):
    with pytest.raises(VerificationUnavailable):
        EffectContract("MarkorDeleteNote", {"file_name": "target"}, PublicFiles({DOCS + "/target": bad_read}))


def move_setup(content=""):
    source, destination = ROOT + "/Download", ROOT + "/DCIM"
    device = PublicFiles({source + "/image.jpg": content, source + "/keep.txt": "source keeper",
                          destination + "/other.txt": "destination keeper"})
    contract = EffectContract("FilesMoveFile", {"file_name": "image.jpg", "source_folder": "Download",
                                               "destination_folder": "/sdcard/DCIM"}, device)
    return device, contract, source + "/image.jpg", destination + "/image.jpg"


@pytest.mark.parametrize("content", ["", "café\n中文\x00"])
def test_move_preserves_empty_and_utf8_file_contents(content):
    device, contract, source, destination = move_setup(content)
    assert contract.check() is False
    device.files[destination] = device.files.pop(source)
    assert contract.check() is True
    assert contract.progress()["expected_effects"]["present"] == destination


@pytest.mark.parametrize("fault", ["copy", "content", "unrelated_loss", "unrelated_change"])
def test_move_requires_source_absence_correct_content_and_other_files(fault):
    device, contract, source, destination = move_setup("real data")
    device.files[destination] = device.files[source]
    if fault != "copy":
        del device.files[source]
    if fault == "content":
        device.files[destination] = "wrong"
    elif fault == "unrelated_loss":
        del device.files[ROOT + "/DCIM/other.txt"]
    elif fault == "unrelated_change":
        device.files[ROOT + "/Download/keep.txt"] = "wrong"
    assert contract.check() is False


def test_move_rejects_missing_source_and_existing_destination():
    params = {"file_name": "f", "source_folder": "Download", "destination_folder": "DCIM"}
    device = PublicFiles({}, [ROOT + "/Download", ROOT + "/DCIM"])
    with pytest.raises(ContractConstructionError, match="source file is missing"):
        EffectContract("FilesMoveFile", params, device)
    device.files.update({ROOT + "/Download/f": "x", ROOT + "/DCIM/f": "old"})
    with pytest.raises(ContractConstructionError, match="overwrite"):
        EffectContract("FilesMoveFile", params, device)


@pytest.mark.parametrize("params", [
    {"file_name": "../secret", "source_folder": "Download", "destination_folder": "DCIM"},
    {"file_name": "f", "source_folder": "/data/data/app", "destination_folder": "DCIM"},
    {"file_name": "f", "source_folder": "Download/../Android", "destination_folder": "DCIM"},
])
def test_private_or_traversal_paths_are_rejected_before_device_reads(params):
    device = PublicFiles({})
    with pytest.raises(ContractConstructionError):
        EffectContract("FilesMoveFile", params, device)
    assert device.calls == []


@pytest.mark.parametrize("bounds", [{"max_depth": 1}, {"max_entries": 1},
                                    {"max_file_bytes": 2}, {"max_total_bytes": 2}])
def test_snapshot_bounds_never_silently_drop_files(bounds):
    files = {DOCS + "/a/b/target": "123", DOCS + "/other": "456"}
    with pytest.raises(VerificationUnavailable):
        EffectContract("MarkorDeleteNote", {"file_name": "target"}, PublicFiles(files), **bounds)


def test_malformed_or_duplicate_listing_is_unknown():
    class BadFiles(PublicFiles):
        def list_files(self, path):
            row = {"name": "target", "path": path + "/target", "is_dir": False}
            return [row, row]
    with pytest.raises(VerificationUnavailable, match="duplicate"):
        EffectContract("MarkorDeleteNote", {"file_name": "target"}, BadFiles({DOCS + "/target": "x"}))


MARKOR = "net.gsantner.markor/activity.MainActivity"
LAUNCHER = "com.google.android.apps.nexuslauncher/NexusLauncherActivity"


def ui(activity=MARKOR, nodes=(), forest=None):
    return {"activity": activity, "action_elements": list(nodes), "ax_forest": forest or {}}


def shade(key="resource_name", visible=True):
    return {key: "com.android.systemui:id/notification_stack_scroller", "is_visible_to_user": visible}


class UI:
    def __init__(self, states):
        self.states = list(states)
        self.actions = []

    def observe(self):
        return copy.deepcopy(self.states[0])

    def _action(self, action):
        self.actions.append(action)
        if len(self.states) > 1:
            self.states.pop(0)

    def navigate_back(self):
        self._action({"action_type": "navigate_back"})

    def open_app(self, app_name):
        self._action({"action_type": "open_app", "app_name": app_name})


@pytest.mark.parametrize("key", ["resource_name", "resource_id", "view_id_resource_name"])
def test_normalize_closes_visible_panel_in_elements_or_forest(key):
    first = ui(forest={"windows": [{"tree": {"nodes": [shade(key)]}}]})
    device = UI([first, ui()])
    result = normalize_ui(device, "Markor")
    assert result["normalized"] and result["shade_detected"]
    assert device.actions == [{"action_type": "navigate_back"}]


@pytest.mark.parametrize("nodes", [
    [{"resource_name": "com.android.systemui:id/clock", "is_visible": True,
      "content_description": "Android System notification: sign in"}],
    [shade(visible=False)],
    [{"resource_name": "other.app:id/notification_panel", "is_visible": True}],
    [{"text": "Notifications", "is_visible": True}],
])
def test_normalize_does_not_mistake_status_bar_or_text_for_expanded_shade(nodes):
    device = UI([ui(nodes=nodes)])
    assert normalize_ui(device, "Markor")["normalized"]
    assert device.actions == []


def test_normalize_can_close_shade_then_restore_focus_with_two_actions():
    device = UI([ui(LAUNCHER, [shade()]), ui(LAUNCHER), ui()])
    result = normalize_ui(device, "Markor")
    assert result["normalized"] and result["action_count"] == 2
    assert device.actions == [{"action_type": "navigate_back"}, {"action_type": "open_app", "app_name": "Markor"}]


def test_normalize_is_bounded_when_panel_does_not_close():
    device = UI([ui(nodes=[shade()])])
    result = normalize_ui(device, "Markor")
    assert not result["normalized"] and result["action_count"] == 2
    assert result["reason"] == "normalization_action_limit"


def test_normalize_unknown_activity_does_not_guess_and_mapping_is_supported():
    device = UI([ui("")])
    assert normalize_ui(device, "Files")["reason"] == "activity_unknown"
    assert device.actions == []
    device = UI([ui(LAUNCHER), ui("sample.app/Main")])
    assert normalize_ui(device, {"app_name": "Sample", "package_name": "sample.app"})["normalized"]
    assert device.actions == [{"action_type": "open_app", "app_name": "Sample"}]


@pytest.mark.parametrize("activity", ["com.google.android.permissioncontroller/GrantPermissionsActivity",
                                      "com.google.android.documentsui/PickerActivity", "other.app/Main"])
def test_normalize_leaves_nonlauncher_activities_untouched(activity):
    device = UI([ui(activity)])
    result = normalize_ui(device, "Markor")
    assert not result["normalized"] and result["reason"] == "other_activity_not_normalized"
    assert device.actions == []

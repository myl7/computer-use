"""Unit tests for the fragility probe: arm apply/revert against a mock adb,
the device fingerprint, the loud/silent coding, and locator tagging.

Zero emulator, zero LLM. The mock adb below models only the commands the arms
actually issue, and models them by effect (a settings namespace, a density
override, a battery override, a notification count, a permission table) so a
revert that restores the wrong thing shows up as a fingerprint mismatch
rather than as a passing test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import fragility_probe, perturb
from guiexp_android.perturb import AdbError
from guiexp_android.program_runtime import ProgramDevice


# ----------------------------------------------------------------- mock adb


class FakeAdb:
    """A device model just deep enough for the arms to be wrong on."""

    def __init__(self, physical_density: int = 440, packages=None):
        self.settings: dict[tuple[str, str], str] = {}
        self.physical_density = physical_density
        self.override_density: int | None = None
        self.battery = {"level": 100, "plugged": True}
        self.notifications: list[dict] = []
        self.shade_expanded = False
        self.permissions: dict[tuple[str, str], bool] = {}
        for family, (package, permission) in perturb.PERMISSION_TARGETS.items():
            self.permissions[(package, permission)] = True
        self.force_stopped: list[str] = []
        self.calls: list[tuple[str, ...]] = []
        self.fail_commands: set[tuple[str, ...]] = set()

    # -- dispatch ----------------------------------------------------------

    def shell(self, *args: str, check: bool = True) -> str:
        args = tuple(str(a) for a in args)
        self.calls.append(args)
        for prefix in self.fail_commands:
            if args[: len(prefix)] == prefix:
                if check:
                    raise AdbError(f"{' '.join(args)}: rc=1 mock failure")
                return ""
        head = args[0]
        handler = getattr(self, f"_cmd_{head.replace('-', '_')}", None)
        if handler is None:
            return ""
        return handler(args[1:])

    def run(self, *args: str, check: bool = True) -> str:
        if args and args[0] == "shell":
            return self.shell(*args[1:], check=check)
        self.calls.append(tuple(str(a) for a in args))
        return ""

    # -- commands ----------------------------------------------------------

    def _cmd_settings(self, args) -> str:
        verb, namespace, key = args[0], args[1], args[2]
        if verb == "get":
            return f"{self.settings.get((namespace, key), 'null')}\n"
        if verb == "put":
            self.settings[(namespace, key)] = args[3]
            return ""
        if verb == "delete":
            self.settings.pop((namespace, key), None)
            return ""
        raise AdbError(f"settings {verb}?")

    def _cmd_wm(self, args) -> str:
        if args[0] != "density":
            return ""
        if len(args) == 1:
            out = f"Physical density: {self.physical_density}\n"
            if self.override_density is not None:
                out += f"Override density: {self.override_density}\n"
            return out
        if args[1] == "reset":
            self.override_density = None
        else:
            self.override_density = int(args[1])
        return ""

    def _cmd_dumpsys(self, args) -> str:
        what = args[0]
        if what == "battery":
            if len(args) == 1:
                return (f"Current Battery Service state:\n"
                        f"  AC powered: {'true' if self.battery['plugged'] else 'false'}\n"
                        f"  level: {self.battery['level']}\n")
            if args[1] == "unplug":
                self.battery["plugged"] = False
            elif args[1] == "reset":
                self.battery = {"level": 100, "plugged": True}
            elif args[1] == "set" and args[2] == "level":
                self.battery["level"] = int(args[3])
            return ""
        if what == "notification":
            return "".join("SBN: pkg=com.android.shell\n" for _ in self.notifications)
        if what == "package":
            package = args[1]
            lines = [f"      {perm}: granted={'true' if granted else 'false'}"
                     for (pkg, perm), granted in sorted(self.permissions.items())
                     if pkg == package]
            return "\n".join(lines) + "\n"
        return ""

    def _cmd_cmd(self, args) -> str:
        service = args[0]
        if service == "uimode" and args[1] == "night":
            # The real command writes the same secure setting.
            self.settings[("secure", "ui_night_mode")] = {"yes": "2", "no": "1"}.get(args[2], "0")
            return f"Night mode: {args[2]}\n"
        if service == "notification" and args[1] == "post":
            self.notifications.append({"argv": args})
            return ""
        if service == "statusbar":
            self.shade_expanded = args[1].startswith("expand")
            return ""
        return ""

    def _cmd_pm(self, args) -> str:
        verb = args[0]
        if verb == "revoke":
            self.permissions[(args[1], args[2])] = False
        elif verb == "grant":
            self.permissions[(args[1], args[2])] = True
        elif verb == "clear" and args[1] == "com.android.shell":
            self.notifications.clear()
        return ""

    def _cmd_am(self, args) -> str:
        if args[0] == "force-stop":
            self.force_stopped.append(args[1])
        return ""


def fresh_arms():
    """A fresh registry per test: arms carry saved state between apply and
    revert, so the module-level singletons must not be shared across tests."""
    return {
        "clean": perturb.CleanArm("clean"),
        "font_large": perturb.SettingsArm("font_large", "system", "font_scale", "1.3"),
        "font_small": perturb.SettingsArm("font_small", "system", "font_scale", "0.85"),
        "density_small": perturb.DensityArm("density_small", 0.85),
        "locale_fr": perturb.LocaleArm("locale_fr", "fr-FR"),
        "dark_theme": perturb.ThemeArm("dark_theme"),
        "notification": perturb.NotificationArm("notification", "T", "B"),
        "low_battery": perturb.BatteryArm("low_battery"),
        "permission_dialog": perturb.PermissionArm("permission_dialog"),
        "update_prompt": perturb.NotificationArm("update_prompt", "U", "V",
                                                 tag="guiexp_update", stand_in=True),
    }


CONTEXT = {"family": "ContactsAddContact", "package": "com.google.android.contacts"}


# ------------------------------------------------------- A. registry shape


def test_registry_names_are_unique_and_cover_the_required_arms():
    names = [arm.name for arm in perturb.ARMS]
    assert len(names) == len(set(names))
    required = {"clean", "font_large", "font_small", "density_small", "locale_fr",
                "dark_theme", "notification", "low_battery", "permission_dialog",
                "update_prompt"}
    assert required <= set(names)
    assert set(perturb.ARMS_BY_NAME) == set(names)


def test_every_arm_declares_scope_kind_and_source():
    for arm in perturb.ARMS:
        described = arm.describe()
        assert described["scope"] in ("session", "step")
        assert described["kind"] in ("baseline", "config", "interruption")
        assert described["source"], f"{arm.name} has no source attribution"
        if described["scope"] == "step":
            assert described["at_step"] is not None


def test_stand_in_arms_are_flagged():
    """An arm that only approximates the source benchmark must say so, or the
    write-up will claim a D-GARA dialog it never showed."""
    by_name = perturb.ARMS_BY_NAME
    assert by_name["update_prompt"].stand_in is True
    assert by_name["permission_dialog"].stand_in is False  # a real runtime dialog
    assert by_name["low_battery"].stand_in is False        # the platform's own warning


def test_select_arms_rejects_a_typo():
    with pytest.raises(ValueError) as exc:
        perturb.select_arms(["font_large", "font_huge"])
    assert "font_huge" in str(exc.value)
    assert [a.name for a in perturb.select_arms(["low_battery", "clean"])] == \
        ["clean", "low_battery"]  # registry order, not argument order


# ------------------------------------------------- B. apply / revert / print


@pytest.mark.parametrize("arm_name", list(fresh_arms()))
def test_arm_round_trip_restores_the_clean_fingerprint(arm_name):
    adb = FakeAdb()
    arm = fresh_arms()[arm_name]
    baseline = perturb.device_fingerprint(adb)
    baseline_hash = perturb.fingerprint_hash(baseline)

    arm_before = arm.fingerprint(adb)
    arm.apply(adb, CONTEXT)
    arm_after = arm.fingerprint(adb)
    if arm_name != "clean" and not arm.skipped_reason:
        assert arm_before != arm_after, f"{arm_name} apply changed nothing"

    arm.revert(adb)
    assert arm.fingerprint(adb) == arm_before
    assert perturb.device_fingerprint(adb) == baseline
    assert perturb.fingerprint_hash(perturb.device_fingerprint(adb)) == baseline_hash


@pytest.mark.parametrize("arm_name", list(fresh_arms()))
def test_arm_round_trip_from_a_non_default_baseline(arm_name):
    """Revert must restore what was there, not a hardcoded default.

    An arm that reverted font_scale by writing "1.0" would pass the test
    above and fail this one, on a device whose user had set 1.15.
    """
    adb = FakeAdb(physical_density=560)
    adb.settings[("system", "font_scale")] = "1.15"
    adb.settings[("system", "system_locales")] = "en-GB"
    adb.settings[("secure", "ui_night_mode")] = "0"
    adb.override_density = 480
    adb.battery = {"level": 73, "plugged": False}
    baseline = perturb.device_fingerprint(adb)

    arm = fresh_arms()[arm_name]
    arm.apply(adb, CONTEXT)
    arm.revert(adb)
    after = perturb.device_fingerprint(adb)
    # Two arms revert by handing control back rather than by writing a value:
    # ``wm density reset`` clears the override flag, and ``dumpsys battery
    # reset`` returns the battery to the emulator's own reading. Neither can
    # restore a pre-existing override, and pretending otherwise would be
    # worse: the probe would report a device state it does not have.
    if arm_name == "density_small":
        assert after["wm_density"]["override"] is None
        baseline = {**baseline, "wm_density": after["wm_density"]}
    if arm_name == "low_battery":
        assert after["battery"]["plugged"] is True
        baseline = {**baseline, "battery": after["battery"]}
    assert after == baseline


def test_font_arms_write_the_documented_values():
    adb = FakeAdb()
    arms = fresh_arms()
    arms["font_large"].apply(adb, CONTEXT)
    assert adb.settings[("system", "font_scale")] == "1.3"
    arms["font_large"].revert(adb)
    arms["font_small"].apply(adb, CONTEXT)
    assert adb.settings[("system", "font_scale")] == "0.85"


def test_density_arm_scales_the_physical_density():
    adb = FakeAdb(physical_density=440)
    arm = fresh_arms()["density_small"]
    arm.apply(adb, CONTEXT)
    assert adb.override_density == round(440 * 0.85)
    arm.revert(adb)
    assert adb.override_density is None


def test_locale_arm_force_stops_only_the_family_app():
    adb = FakeAdb()
    arm = fresh_arms()["locale_fr"]
    arm.apply(adb, {"family": "MarkorCreateNote"})
    assert adb.settings[("system", "system_locales")] == "fr-FR"
    assert adb.force_stopped == ["net.gsantner.markor"]
    arm.revert(adb)
    assert ("system", "system_locales") not in adb.settings


def test_locale_arm_without_a_family_stops_every_target_app():
    adb = FakeAdb()
    arm = fresh_arms()["locale_fr"]
    arm.apply(adb, {})
    assert set(adb.force_stopped) == set(perturb.FAMILY_PACKAGES.values())


def test_theme_arm_falls_back_to_settings_when_cmd_uimode_is_missing():
    adb = FakeAdb()
    adb.fail_commands.add(("cmd", "uimode"))
    arm = fresh_arms()["dark_theme"]
    arm.apply(adb, CONTEXT)
    assert adb.settings[("secure", "ui_night_mode")] == "2"
    arm.revert(adb)
    assert ("secure", "ui_night_mode") not in adb.settings


def test_battery_arm_unplugs_and_drops_the_level():
    adb = FakeAdb()
    arm = fresh_arms()["low_battery"]
    arm.apply(adb, CONTEXT)
    assert adb.battery == {"level": 5, "plugged": False}
    arm.revert(adb)
    assert adb.battery == {"level": 100, "plugged": True}


def test_notification_arm_posts_and_clears():
    adb = FakeAdb()
    arm = fresh_arms()["notification"]
    arm.apply(adb, CONTEXT)
    assert perturb.count_shell_notifications(adb) == 1
    arm.revert(adb)
    assert perturb.count_shell_notifications(adb) == 0
    assert adb.shade_expanded is False


def test_permission_arm_revokes_a_startup_grant_and_regrants_it():
    adb = FakeAdb()
    arm = fresh_arms()["permission_dialog"]
    arm.apply(adb, CONTEXT)
    key = ("com.google.android.contacts", "android.permission.POST_NOTIFICATIONS")
    assert adb.permissions[key] is False
    assert "com.google.android.contacts" in adb.force_stopped
    arm.revert(adb)
    assert adb.permissions[key] is True


def test_permission_arm_skips_a_family_with_no_pre_granted_permission():
    adb = FakeAdb()
    arm = fresh_arms()["permission_dialog"]
    arm.apply(adb, {"family": "FilesMoveFile"})
    assert arm.skipped_reason and "FilesMoveFile" in arm.skipped_reason
    assert arm.applied is False
    assert all(adb.permissions.values())  # nothing was touched


def test_permission_arm_targets_match_the_startup_grants():
    """The arm exists to undo one of android_env.STARTUP_GRANTS. If a grant is
    renamed there and not here, the arm silently stops reproducing the AVD's
    own first-episode dialog."""
    from guiexp_android import android_env

    granted = {(cmd[3], cmd[4]) for cmd in android_env.STARTUP_GRANTS
               if cmd[:3] == ("shell", "pm", "grant")}
    for package, permission in perturb.PERMISSION_TARGETS.values():
        assert (package, permission) in granted


# ------------------------------------------------------ C. session and step


def test_arm_session_reverts_even_when_the_body_raises():
    adb = FakeAdb()
    arm = fresh_arms()["font_large"]
    baseline = perturb.device_fingerprint(adb)
    with pytest.raises(RuntimeError):
        with perturb.arm_session(arm, adb, CONTEXT):
            assert adb.settings[("system", "font_scale")] == "1.3"
            raise RuntimeError("replay died mid program")
    assert perturb.device_fingerprint(adb) == baseline


def test_arm_session_yields_before_and_applied_fingerprints():
    adb = FakeAdb()
    arm = fresh_arms()["low_battery"]
    with perturb.arm_session(arm, adb, CONTEXT) as state:
        assert state["skipped"] is None
        assert state["applied"]["battery"]["level"] == 5
        assert state["before"]["battery"]["level"] == 100


def test_step_injector_fires_at_the_scheduled_action():
    adb = FakeAdb()
    arm = fresh_arms()["notification"]
    injector = perturb.StepInjector(arm, adb, CONTEXT, at_step=2)
    injector.after_action(1)
    assert injector.fired_at is None and perturb.count_shell_notifications(adb) == 0
    injector.after_action(2)
    assert injector.fired_at == 2 and perturb.count_shell_notifications(adb) == 1
    injector.after_action(3)
    assert injector.fired_at == 2  # fires once
    injector.revert()
    assert perturb.count_shell_notifications(adb) == 0
    assert injector.record()["fired"] is True


def test_step_injector_force_fire_covers_a_program_that_dies_early():
    adb = FakeAdb()
    arm = fresh_arms()["low_battery"]
    injector = perturb.StepInjector(arm, adb, CONTEXT, at_step=5)
    injector.after_action(1)
    assert injector.fired_at is None
    injector.force_fire()
    assert injector.fired_at == 5
    assert adb.battery["level"] == 5
    injector.revert()
    assert adb.battery["level"] == 100


def test_step_injector_records_an_apply_failure_instead_of_raising():
    adb = FakeAdb()
    adb.fail_commands.add(("dumpsys", "battery", "unplug"))
    injector = perturb.StepInjector(fresh_arms()["low_battery"], adb, CONTEXT, at_step=1)
    injector.after_action(1)
    assert injector.error and "AdbError" in injector.error


# ------------------------------------------------------- D. fingerprinting


def test_device_fingerprint_covers_every_axis_an_arm_can_move():
    adb = FakeAdb()
    fingerprint = perturb.device_fingerprint(adb)
    assert set(fingerprint) == {
        "system.font_scale", "system.system_locales", "secure.ui_night_mode",
        "wm_density", "battery", "shell_notifications",
    }


def test_fingerprint_hash_is_stable_and_moves_with_the_state():
    adb = FakeAdb()
    first = perturb.fingerprint_hash(perturb.device_fingerprint(adb))
    assert first == perturb.fingerprint_hash(perturb.device_fingerprint(adb))
    arm = fresh_arms()["font_large"]
    arm.apply(adb, CONTEXT)
    assert perturb.fingerprint_hash(perturb.device_fingerprint(adb)) != first
    arm.revert(adb)
    assert perturb.fingerprint_hash(perturb.device_fingerprint(adb)) == first


def test_fingerprint_hash_ignores_key_order():
    a = {"x": 1, "y": {"b": 2, "a": 3}}
    b = {"y": {"a": 3, "b": 2}, "x": 1}
    assert perturb.fingerprint_hash(a) == perturb.fingerprint_hash(b)


# --------------------------------------------------- E. loud/silent coding


@pytest.mark.parametrize("error,reward,expected", [
    (None, 1.0, "PASS"),
    (None, 0.0, "SILENT_WRONG"),
    ("RuntimeError: no Save button", 0.0, "LOUD"),
    ("RuntimeError: editor did not close", 1.0, "FALSE_ALARM"),
])
def test_outcome_coding(error, reward, expected):
    assert fragility_probe.outcome_of(error, reward) == expected


def test_loudness_split():
    assert fragility_probe.is_loud("LOUD") is True
    assert fragility_probe.is_loud("FALSE_ALARM") is True
    assert fragility_probe.is_loud("SILENT_WRONG") is False
    assert fragility_probe.is_loud("PASS") is False


def test_exception_type_extraction():
    assert fragility_probe.exception_type("ValueError: click: no element") == "ValueError"
    assert fragility_probe.exception_type("reset: RuntimeError: adb noise") == "reset"
    assert fragility_probe.exception_type(None) is None


# ------------------------------------------------------- F. locator tagging


@pytest.mark.parametrize("criteria,kind", [
    ({"text": "Save"}, "text"),
    ({"contains": "Create new contact"}, "text"),
    ({"hint": "First name"}, "text"),
    ({"description": "Save"}, "content_desc"),
    ({"tooltip": "Save"}, "content_desc"),
    ({"resource_id": "com.example:id/save"}, "resource_id"),
    ({"index": 4}, "coordinate"),
    ({"clickable": True}, "none"),
    ({}, "none"),
    ({"text": None, "description": "Back"}, "content_desc"),
    ({"description": "Save", "text": "Save"}, "text"),
    ({"description": "Contacts", "clickable": True}, "content_desc"),
])
def test_classify_criteria(criteria, kind):
    assert fragility_probe.classify_criteria(criteria) == kind


def element(index, text="", hint="", description="", editable=False, clickable=True):
    return {"index": index, "text": text, "hint": hint, "description": description,
            "tooltip": "", "editable": editable, "clickable": clickable,
            "long_clickable": False, "scrollable": False, "focusable": False,
            "selected": False, "checked": False}


class FakeDevice(ProgramDevice):
    """ProgramDevice with the emulator cut out: canned screens, logged actions.

    Subclassed rather than reimplemented so the real ``find`` and
    ``_require_index`` run, which is what the locator tagging observes.
    """

    def __init__(self, screens):
        self._screens = [list(s) for s in screens]
        self._at = 0
        self._settle_s = 0.0
        self._max_actions = 100
        self._actions = 0
        self.executed: list[dict] = []

    def elements(self):
        return self._screens[min(self._at, len(self._screens) - 1)]

    def execute(self, action):
        self._actions += 1
        self.executed.append(dict(action))
        self._at += 1

    def current_activity(self):
        return "com.example/.Fake"

    def settle(self, seconds=0.0):
        return None

    def wait(self):
        self.execute({"action_type": "wait"})


class TracedFake(fragility_probe.TracingMixin, FakeDevice):
    def __init__(self, screens, injector=None):
        FakeDevice.__init__(self, screens)
        self.injector = injector


def test_tracing_records_hits_misses_and_kinds():
    screens = [[element(0, text="Save"), element(1, description="Back")]]
    device = TracedFake(screens)
    device.click(text="Save")
    device.find(description="Back")
    device.find(text="Nope")
    kinds = [(t["kind"], t["hit"]) for t in device.locator_trace]
    assert kinds == [("text", True), ("content_desc", True), ("text", False)]


def test_tracing_records_a_direct_index_as_a_coordinate_locator():
    device = TracedFake([[element(0, text="Save")]])
    device.click(2)
    assert [t["kind"] for t in device.locator_trace] == ["coordinate"]
    assert device.executed == [{"action_type": "click", "index": 2}]


def test_failing_locator_blames_the_last_miss_not_the_last_call():
    """Compiled programs wrap finds in try/except ladders, so the raise names
    a different line than the locator that actually failed."""
    device = TracedFake([[element(0, text="Save")]])
    device.find(description="Create contact")   # miss
    device.find(text="Save")                    # hit, later in the ladder
    blame = fragility_probe.failing_locator(device.locator_trace, "LOUD")
    assert blame["kind"] == "content_desc"
    assert blame["reason"] == "last locator miss"


def test_failing_locator_reports_none_when_every_locator_hit():
    device = TracedFake([[element(0, text="Save")]])
    device.find(text="Save")
    blame = fragility_probe.failing_locator(device.locator_trace, "SILENT_WRONG")
    assert blame["kind"] == "none"
    assert "not a locator failure" in blame["reason"]


def test_failing_locator_on_an_empty_trace_and_on_a_pass():
    assert fragility_probe.failing_locator([], "LOUD")["kind"] == "none"
    assert fragility_probe.failing_locator([], "PASS")["kind"] is None


# ------------------------------------------------- G. replay through a mock


class FakeRunner:
    """ProgramRunner stand-in: builds the probe's own device, runs the
    program, and returns a caller-chosen oracle verdict."""

    def __init__(self, screens, reward: float = 1.0):
        self.screens = screens
        self.reward = reward
        self.devices: list = []

    def run(self, program, binding, family, judge_params=None, device_factory=None, **_kw):
        env = SimpleNamespace(screens=self.screens)
        device = device_factory(env) if device_factory else FakeDevice(self.screens)
        self.devices.append(device)
        error = None
        try:
            program(device, dict(binding))
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            error = f"{type(exc).__name__}: {exc}"
        return {"passed": self.reward >= 1.0 and error is None, "error": error,
                "reward": self.reward, "device": device}


@pytest.fixture()
def traced_device_cls(monkeypatch):
    """Swap the probe's TracingDevice for one that needs no emulator."""

    class _Device(TracedFake):
        def __init__(self, env, injector=None, **_kw):
            TracedFake.__init__(self, env.screens, injector=injector)

    monkeypatch.setattr(fragility_probe, "TracingDevice", _Device)
    return _Device


DRAW = {"params": {"name": "Ada Lovelace", "number": "+1"},
        "binding": {"name": "Ada Lovelace", "number": "+1"}}


def test_replay_binding_tags_a_loud_text_locator_failure(traced_device_cls):
    def program(device, binding):
        device.click(text="Create new contact")
        return True

    adb = FakeAdb()
    runner = FakeRunner([[element(0, text="Contacts")]], reward=0.0)
    record = fragility_probe.replay_binding(
        program, "ContactsAddContact", DRAW, runner,
        perturb.ARMS_BY_NAME["clean"], adb, CONTEXT)
    assert record["outcome"] == "LOUD"
    assert record["loud"] is True
    assert record["exception_type"] == "ValueError"
    assert record["locator"]["kind"] == "text"
    assert record["injection"] is None


def test_replay_binding_tags_a_silent_failure(traced_device_cls):
    def program(device, binding):
        device.click(text="Save")
        return True

    adb = FakeAdb()
    runner = FakeRunner([[element(0, text="Save")]], reward=0.0)
    record = fragility_probe.replay_binding(
        program, "ContactsAddContact", DRAW, runner,
        perturb.ARMS_BY_NAME["clean"], adb, CONTEXT)
    assert record["outcome"] == "SILENT_WRONG"
    assert record["loud"] is False
    assert record["locator"]["kind"] == "none"


def test_replay_binding_fires_and_reverts_a_step_arm(traced_device_cls):
    def program(device, binding):
        for _ in range(4):
            device.click(0)
        return True

    adb = FakeAdb()
    runner = FakeRunner([[element(0, text="Save")]], reward=1.0)
    arm = perturb.NotificationArm("notification", "T", "B", at_step=2)
    record = fragility_probe.replay_binding(
        program, "ContactsAddContact", DRAW, runner, arm, adb, CONTEXT)
    assert record["outcome"] == "PASS"
    assert record["injection"]["fired_at"] == 2
    assert perturb.count_shell_notifications(adb) == 0  # reverted
    assert record["actions"] == 4


def test_replay_binding_force_fires_when_the_program_dies_early(traced_device_cls):
    def program(device, binding):
        device.click(text="Missing")
        return True

    adb = FakeAdb()
    runner = FakeRunner([[element(0, text="Save")]], reward=0.0)
    arm = perturb.NotificationArm("notification", "T", "B", at_step=3)
    record = fragility_probe.replay_binding(
        program, "ContactsAddContact", DRAW, runner, arm, adb, CONTEXT)
    assert record["injection"]["fired"] is True
    assert record["injection"]["fired_at"] == 3
    assert record["outcome"] == "LOUD"


def test_run_arm_holds_a_session_arm_and_reverts_it(traced_device_cls):
    seen: list[str | None] = []

    def program(device, binding):
        seen.append(adb.settings.get(("system", "font_scale")))
        device.click(text="Save")
        return True

    adb = FakeAdb()
    runner = FakeRunner([[element(0, text="Save")]], reward=1.0)
    arm = perturb.SettingsArm("font_large", "system", "font_scale", "1.3")
    entry = fragility_probe.run_arm(program, "ContactsAddContact",
                                    [DRAW, DRAW], runner, arm, adb, verbose=False)
    assert seen == ["1.3", "1.3"]  # held for the whole block
    assert entry["n"] == 2 and entry["pass"] == 2
    assert entry["fingerprint"]["reverted_clean"] is True
    assert ("system", "font_scale") not in adb.settings


def test_run_arm_records_a_skipped_arm_without_running_anything(traced_device_cls):
    def program(device, binding):
        raise AssertionError("should not run")

    adb = FakeAdb()
    runner = FakeRunner([[element(0)]], reward=1.0)
    entry = fragility_probe.run_arm(program, "FilesMoveFile", [DRAW],
                                    runner, perturb.PermissionArm("permission_dialog"),
                                    adb, verbose=False)
    assert entry["skipped"]
    assert entry["runs"] == [] and entry["n"] == 0
    assert entry["pass_rate"] is None


# ---------------------------------------------------------- H. aggregation


def _run(binding_name: str, outcome: str, kind: str | None = None) -> dict:
    return {
        "binding": {"name": binding_name},
        "outcome": outcome,
        "passed": outcome == "PASS",
        "loud": fragility_probe.is_loud(outcome),
        "locator": {"kind": kind},
    }


def test_counts_and_loud_share():
    runs = [_run("a", "PASS"), _run("b", "LOUD"), _run("c", "SILENT_WRONG"),
            _run("d", "LOUD")]
    row = fragility_probe.counts(runs)
    assert (row["n"], row["pass"], row["loud"], row["silent_wrong"]) == (4, 1, 2, 1)
    assert row["pass_rate"] == 0.25
    assert row["loud_share"] == pytest.approx(2 / 3)


def test_robust_success_rate_is_conditioned_on_the_clean_pass_set():
    clean = [_run("a", "PASS"), _run("b", "PASS"), _run("c", "LOUD")]
    arm = [_run("a", "PASS"), _run("b", "LOUD"), _run("c", "PASS")]
    # c never passed clean, so its arm pass must not inflate the RSR.
    assert fragility_probe.robust_success_rate(clean, arm) == 0.5
    assert fragility_probe.robust_success_rate([_run("a", "LOUD")], arm) is None


def test_summarize_reports_break_probability_and_locator_blame():
    family = {
        "family": "ContactsAddContact",
        "arms": [
            {"arm": perturb.CleanArm("clean").describe(),
             "runs": [_run("a", "PASS"), _run("b", "PASS")]},
            {"arm": perturb.LocaleArm("locale_fr", "fr-FR").describe(),
             "runs": [_run("a", "LOUD", "text"), _run("b", "LOUD", "text")]},
            {"arm": perturb.ThemeArm("dark_theme").describe(),
             "runs": [_run("a", "PASS"), _run("b", "PASS")]},
        ],
    }
    summary = fragility_probe.summarize([family])
    assert summary["per_arm"]["clean"]["pass_rate"] == 1.0
    assert summary["per_arm"]["locale_fr"]["rsr"] == 0.0
    assert summary["per_arm"]["locale_fr"]["break_given_change"] == 1.0
    assert summary["per_arm"]["dark_theme"]["break_given_change"] == 0.0
    assert summary["break_given_change_uniform"] == 0.5
    assert summary["q_clean"] == 0.0
    assert summary["loud_total"] == 2 and summary["silent_total"] == 0
    assert summary["locator_blame"]["locale_fr"]["text"] == 2
    assert summary["locator_blame"]["dark_theme"]["text"] == 0
    table = fragility_probe.table_md(summary)
    assert "| locale_fr |" in table and "resource_id" in table


# -------------------------------------------------- I. program resolution


def _write(path: Path, text: str = "def program(device, binding):\n    return True\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_find_program_prefers_t16_verified_over_t14(tmp_path):
    slug = "z-ai_glm-5.3-flash"
    _write(tmp_path / "t16_build" / slug / "ContactsAddContact" / "verified_program.py")
    _write(tmp_path / "t14_compilepath" / slug / "contacts" / "attempt1" / "family_program.py")
    found = fragility_probe.find_program("ContactsAddContact", "z-ai/glm-5.3-flash", tmp_path)
    assert found.parent.parent.parent.name == "t16_build"


def test_find_program_falls_back_to_the_t14_best_attempt(tmp_path):
    slug = "z-ai_glm-5.3-flash"
    root = tmp_path / "t14_compilepath" / slug
    for attempt in (1, 2, 3):
        _write(root / "markor" / f"attempt{attempt}" / "family_program.py")
    (root / "summary.json").write_text(json.dumps(
        {"families": {"markor": {"family": "MarkorCreateNote", "best_attempt": 2}}}))
    found = fragility_probe.find_program("MarkorCreateNote", "z-ai/glm-5.3-flash", tmp_path)
    assert found.parent.name == "attempt2"


def test_find_program_raises_a_useful_error_when_nothing_is_compiled(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        fragility_probe.find_program("OsmAndMarker", "z-ai/glm-5.3-flash", tmp_path)
    assert "OsmAndMarker" in str(exc.value)


def test_probe_bindings_gate_namespace_matches_gate_runner():
    from guiexp_android.gate_runner import heldout_bindings

    gate = heldout_bindings("ContactsAddContact", 5, seed=0)
    probe = fragility_probe.probe_bindings("ContactsAddContact", 5, 0, "gate")
    assert [d["binding"] for d in probe] == [d["binding"] for d in gate]


def test_probe_bindings_fresh_namespace_is_disjoint_from_the_gate():
    gate = fragility_probe.probe_bindings("ContactsAddContact", 5, 0, "gate")
    fresh = fragility_probe.probe_bindings("ContactsAddContact", 5, 0, "fresh")
    gate_keys = {json.dumps(d["binding"], sort_keys=True) for d in gate}
    fresh_keys = {json.dumps(d["binding"], sort_keys=True) for d in fresh}
    assert len(fresh_keys) == 5
    assert not (gate_keys & fresh_keys)


def test_dry_run_plans_without_touching_a_device(tmp_path, capsys, monkeypatch):
    slug = "z-ai_glm-5.3-flash"
    _write(tmp_path / "t16_build" / slug / "ContactsAddContact" / "verified_program.py")
    monkeypatch.setattr(fragility_probe, "RESULTS_ROOT", tmp_path)
    rc = fragility_probe.main([
        "--family", "ContactsAddContact", "--model", "z-ai/glm-5.3-flash",
        "--arms", "clean", "locale_fr", "--k", "3", "--dry-run",
        "--out-dir", str(tmp_path / "out"),
    ])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["replays"] == 6 and plan["model_tokens"] == 0
    assert len(plan["bindings"]["ContactsAddContact"]) == 3


def test_arm_table_prints_every_arm(capsys):
    assert perturb.main([]) == 0
    out = capsys.readouterr().out
    for arm in perturb.ARMS:
        assert arm.name in out

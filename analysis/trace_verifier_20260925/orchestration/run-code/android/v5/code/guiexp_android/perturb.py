"""Perturbation arms for the Android fragility probe.

Arms are the instrumentation half of the two source benchmarks in
docs/ui-instability-benchmarks.md, ported to plain adb so that nothing has to
be installed on the AVD:

  * B-MoCA (A8) varies device configuration around an unchanged app: font
    size, display density, system language, theme. Those become the "config"
    arms here. B-MoCA also randomizes icon placement and device type; icon
    placement is out of reach without replacing the launcher, and device type
    means a second AVD, so both are left to the runbook rather than faked.
  * D-GARA (A3) injects interruptions at a chosen point of the trajectory
    from a standalone APK: runtime permission dialog, low battery, update
    prompt, incoming notification. Those become the "interruption" arms. We
    do not ship the APK. Each interruption is reproduced with a shell command
    that reaches the same on-screen artifact, and the two arms that are only
    an approximation are flagged ``stand_in=True`` so the write-up cannot
    claim more than was measured.

Every arm is three things.

  ``apply(adb, context)``   mutate device state, after first reading back the
                            value it is about to overwrite
  ``revert(adb)``           restore exactly what was read back (delete the
                            key again if it was unset before)
  ``fingerprint(adb)``      probe the device for the state this arm controls,
                            so the run log records what the device actually
                            was rather than what we asked for

``session`` arms are applied once around a block of replays. ``step`` arms
fire in the middle of one replay, after a chosen number of executed actions,
which is D-GARA's "injected at any chosen point in the trajectory".

Nothing here starts or stops an emulator. Use it against a device that is
already attached.

    from guiexp_android.perturb import Adb, ARMS, arm_session
    adb = Adb()
    with arm_session(ARMS_BY_NAME["font_large"], adb, {"family": "MarkorCreateNote"}):
        ...  # replay under a 1.3 font scale
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from . import android_env

# The apps the seven families drive. Used by the locale arm (an app only
# re-reads the locale when it is (re)launched) and by the permission arm.
FAMILY_PACKAGES: dict[str, str] = {
    "ContactsAddContact": "com.google.android.contacts",
    "SimpleCalendarAddOneEvent": "com.simplemobiletools.calendar.pro",
    "MarkorCreateNote": "net.gsantner.markor",
    "MarkorDeleteNote": "net.gsantner.markor",
    "OsmAndFavorite": "net.osmand",
    "OsmAndMarker": "net.osmand",
    "FilesMoveFile": "com.google.android.documentsui",
}

# The permission arm undoes exactly one of android_env.STARTUP_GRANTS, so the
# runtime dialog it produces is the dialog that AVD would have shown on a
# cold first episode. Families whose app is not in this map skip the arm and
# are recorded as not applicable rather than as a pass.
PERMISSION_TARGETS: dict[str, tuple[str, str]] = {
    "ContactsAddContact": ("com.google.android.contacts",
                           "android.permission.POST_NOTIFICATIONS"),
    "SimpleCalendarAddOneEvent": ("com.simplemobiletools.calendar.pro",
                                  "android.permission.POST_NOTIFICATIONS"),
    "OsmAndFavorite": ("net.osmand", "android.permission.ACCESS_FINE_LOCATION"),
    "OsmAndMarker": ("net.osmand", "android.permission.ACCESS_FINE_LOCATION"),
}

DEFAULT_STEP = 2  # actions executed before a step arm fires


class AdbError(RuntimeError):
    """A non-zero adb exit, carrying the command and stderr."""


class Adb:
    """``adb -s <serial> ...``, with the raw device serial (not the shim).

    The probe talks to the device directly rather than through
    android_env.ADB_SHIM_PATH: the shim only exists to rewrite broken
    ``monkey`` launches for android_world, and none of these commands is a
    monkey launch.
    """

    def __init__(self, adb_path: str | Path | None = None, serial: str | None = None,
                 timeout: float = 30.0):
        self.adb_path = str(adb_path or android_env.ADB_PATH)
        self.serial = serial or android_env.SERIAL
        self.timeout = timeout
        self.calls: list[list[str]] = []

    # A negative return code means the adb client died of a signal before it
    # could talk to the device (the fork-side crash forksafe.py describes).
    # Nothing was executed on the device, so the call is safe to repeat.
    SIGNAL_RETRIES = 3
    SIGNAL_RETRY_SLEEP_S = 1.0

    def run(self, *args: str, check: bool = True) -> str:
        cmd = [self.adb_path, "-s", self.serial, *[str(a) for a in args]]
        self.calls.append(list(args))
        for attempt in range(self.SIGNAL_RETRIES + 1):
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            if out.returncode >= 0 or attempt == self.SIGNAL_RETRIES:
                break
            time.sleep(self.SIGNAL_RETRY_SLEEP_S)
        if check and out.returncode != 0:
            raise AdbError(f"{' '.join(args)}: rc={out.returncode} {out.stderr.strip()[:200]}")
        return out.stdout

    def shell(self, *args: str, check: bool = True) -> str:
        return self.run("shell", *args, check=check)


# -- small shell helpers (module level so a mock adb only needs .shell) ------


def settings_get(adb, namespace: str, key: str) -> str | None:
    """``settings get`` with Android's "null" spelled as None."""
    value = (adb.shell("settings", "get", namespace, key) or "").strip()
    return None if value in ("", "null") else value


def settings_put(adb, namespace: str, key: str, value: str) -> None:
    adb.shell("settings", "put", namespace, key, str(value))


def settings_delete(adb, namespace: str, key: str) -> None:
    adb.shell("settings", "delete", namespace, key)


def settings_restore(adb, namespace: str, key: str, value: str | None) -> None:
    """Put ``value`` back, or delete the key if it was unset to begin with."""
    if value is None:
        settings_delete(adb, namespace, key)
    else:
        settings_put(adb, namespace, key, value)


_PHYSICAL_DENSITY_RE = re.compile(r"Physical density:\s*(\d+)")
_OVERRIDE_DENSITY_RE = re.compile(r"Override density:\s*(\d+)")


def read_density(adb) -> dict:
    out = adb.shell("wm", "density") or ""
    physical = _PHYSICAL_DENSITY_RE.search(out)
    override = _OVERRIDE_DENSITY_RE.search(out)
    return {
        "physical": int(physical.group(1)) if physical else None,
        "override": int(override.group(1)) if override else None,
    }


_BATTERY_LEVEL_RE = re.compile(r"^\s*level:\s*(\d+)", re.MULTILINE)
_BATTERY_PLUGGED_RE = re.compile(r"^\s*(?:AC powered|USB powered):\s*(true|false)", re.MULTILINE)


def read_battery(adb) -> dict:
    out = adb.shell("dumpsys", "battery") or ""
    level = _BATTERY_LEVEL_RE.search(out)
    plugged = _BATTERY_PLUGGED_RE.search(out)
    return {
        "level": int(level.group(1)) if level else None,
        "plugged": (plugged.group(1) == "true") if plugged else None,
    }


def read_night_mode(adb) -> str | None:
    """``settings get secure ui_night_mode``: 1 off, 2 yes, 0/3 auto."""
    return settings_get(adb, "secure", "ui_night_mode")


def count_shell_notifications(adb) -> int:
    """How many notifications ``cmd notification post`` has left standing.

    ``dumpsys notification`` prints one record per posted notification with
    its package; the shell command posts as com.android.shell.
    """
    out = adb.shell("dumpsys", "notification", "--noredact", check=False) or ""
    return len(re.findall(r"com\.android\.shell", out))


# -- arms -------------------------------------------------------------------


class Arm:
    """One perturbation: apply, revert, fingerprint.

    ``kind``    baseline | config | interruption (the two source benchmarks
                split exactly here: config is B-MoCA, interruption is D-GARA)
    ``scope``   session (held for a block of replays) | step (fired mid
                replay after ``at_step`` executed actions)
    ``stand_in``True when the arm only approximates the source benchmark's
                artifact, so the write-up has to say so.
    """

    kind = "config"
    scope = "session"
    stand_in = False
    source = ""
    at_step: int | None = None

    def __init__(self, name: str):
        self.name = name
        self._saved: dict = {}
        self.applied = False
        self.skipped_reason: str | None = None

    # -- interface ---------------------------------------------------------

    def apply(self, adb, context: dict | None = None) -> dict:
        """Mutate the device. Returns the post-apply fingerprint."""
        self.skipped_reason = self._skip_reason(context or {})
        if self.skipped_reason:
            return self.fingerprint(adb)
        self._apply(adb, context or {})
        self.applied = True
        return self.fingerprint(adb)

    def revert(self, adb) -> dict:
        """Undo exactly what apply saved. Safe to call when apply was skipped
        or raised part way: nothing saved means nothing to restore."""
        if self.applied:
            self._revert(adb)
        self.applied = False
        self._saved = {}
        return self.fingerprint(adb)

    def fingerprint(self, adb) -> dict:
        return {}

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "scope": self.scope,
            "at_step": self.at_step,
            "stand_in": self.stand_in,
            "source": self.source,
        }

    # -- subclass hooks ----------------------------------------------------

    def _apply(self, adb, context: dict) -> None:
        raise NotImplementedError

    def _revert(self, adb) -> None:
        raise NotImplementedError

    def _skip_reason(self, context: dict) -> str | None:
        return None


class CleanArm(Arm):
    """The baseline. Touches nothing, but still fingerprints, which is how a
    drifting device is caught: a clean fingerprint that differs between two
    families means an earlier arm did not revert."""

    kind = "baseline"
    source = "baseline"

    def _apply(self, adb, context: dict) -> None:
        return None

    def _revert(self, adb) -> None:
        return None

    def fingerprint(self, adb) -> dict:
        return device_fingerprint(adb)


class SettingsArm(Arm):
    """One ``settings put <ns> <key> <value>``, restored on revert."""

    def __init__(self, name: str, namespace: str, key: str, value: str,
                 kind: str = "config", source: str = ""):
        super().__init__(name)
        self.namespace = namespace
        self.key = key
        self.value = str(value)
        self.kind = kind
        self.source = source

    def _apply(self, adb, context: dict) -> None:
        self._saved["value"] = settings_get(adb, self.namespace, self.key)
        settings_put(adb, self.namespace, self.key, self.value)

    def _revert(self, adb) -> None:
        settings_restore(adb, self.namespace, self.key, self._saved.get("value"))

    def fingerprint(self, adb) -> dict:
        return {f"{self.namespace}.{self.key}": settings_get(adb, self.namespace, self.key)}


class DensityArm(Arm):
    """``wm density``, as a factor of the AVD's physical density.

    Reverted with ``wm density reset`` rather than by writing the physical
    value back, because reset clears the override flag as well; the saved
    override (usually absent) is only there for the fingerprint to check.
    """

    source = "B-MoCA A8 (device configuration)"

    def __init__(self, name: str, factor: float):
        super().__init__(name)
        self.factor = float(factor)

    def _apply(self, adb, context: dict) -> None:
        current = read_density(adb)
        self._saved["density"] = current
        physical = current.get("physical")
        if not physical:
            raise AdbError("wm density did not report a physical density")
        target = int(round(physical * self.factor))
        adb.shell("wm", "density", str(target))

    def _revert(self, adb) -> None:
        adb.shell("wm", "density", "reset")

    def fingerprint(self, adb) -> dict:
        return {"wm_density": read_density(adb)}


class LocaleArm(Arm):
    """System language change without root.

    Route: ``settings put system system_locales <bcp47 list>``. This is the
    only route that survives on a stock, non-rooted image. Two limits, both
    real and both documented in the runbook:

      1. Already running processes keep the old configuration. The arm
         force-stops the family's app so the next launch reads the new list.
         The launcher and system UI are NOT restarted, so the home screen
         stays in the old language.
      2. android.intent.action.LOCALE_CHANGED is a protected broadcast and
         cannot be sent from the shell uid, so no broadcast is attempted.

    The consequence for the measurement is that this arm changes the language
    of the app under test, which is exactly the surface a compiled program's
    text locators address, and leaves the shell around it in English.
    """

    source = "B-MoCA A8 (system language)"

    def __init__(self, name: str, locale: str, packages: Iterable[str] | None = None):
        super().__init__(name)
        self.locale = locale
        self.packages = tuple(packages) if packages else None

    def _targets(self, context: dict) -> tuple[str, ...]:
        if self.packages:
            return self.packages
        family = context.get("family")
        package = FAMILY_PACKAGES.get(family or "")
        return (package,) if package else tuple(sorted(set(FAMILY_PACKAGES.values())))

    def _apply(self, adb, context: dict) -> None:
        self._saved["value"] = settings_get(adb, "system", "system_locales")
        self._saved["packages"] = self._targets(context)
        settings_put(adb, "system", "system_locales", self.locale)
        for package in self._saved["packages"]:
            adb.shell("am", "force-stop", package, check=False)

    def _revert(self, adb) -> None:
        settings_restore(adb, "system", "system_locales", self._saved.get("value"))
        for package in self._saved.get("packages", ()):  # relaunch reads English again
            adb.shell("am", "force-stop", package, check=False)

    def fingerprint(self, adb) -> dict:
        return {"system.system_locales": settings_get(adb, "system", "system_locales")}


class ThemeArm(Arm):
    """Dark theme.

    ``cmd uimode night yes`` is the supported route on API 29 and up and is
    tried first. Images where the uimode shell command is missing fall back
    to ``settings put secure ui_night_mode 2``, which is the value the uimode
    command writes anyway. Whichever route took, revert restores the
    ui_night_mode value read before the change.
    """

    source = "B-MoCA A8 (appearance), our own drift probe dark_theme arm"

    def __init__(self, name: str = "dark_theme", night: str = "yes"):
        super().__init__(name)
        self.night = night

    def _apply(self, adb, context: dict) -> None:
        self._saved["ui_night_mode"] = read_night_mode(adb)
        try:
            out = adb.shell("cmd", "uimode", "night", self.night)
            self._saved["route"] = "cmd uimode"
        except AdbError:
            out = ""
            self._saved["route"] = "settings"
        if self._saved["route"] == "settings" or "Error" in (out or ""):
            self._saved["route"] = "settings"
            settings_put(adb, "secure", "ui_night_mode", "2" if self.night == "yes" else "1")

    def _revert(self, adb) -> None:
        saved = self._saved.get("ui_night_mode")
        if self._saved.get("route") == "cmd uimode":
            adb.shell("cmd", "uimode", "night", "no", check=False)
        settings_restore(adb, "secure", "ui_night_mode", saved)

    def fingerprint(self, adb) -> dict:
        return {"secure.ui_night_mode": read_night_mode(adb)}


class BatteryArm(Arm):
    """D-GARA's system-resource interruption: unplug and drop to 5 percent.

    ``dumpsys battery set level`` plus ``unplug`` makes the framework post
    its own low-battery warning, so the on-screen artifact is the platform's,
    not one we drew. ``dumpsys battery reset`` hands control back to the
    emulator, which is the only correct revert: writing a level back would
    leave the battery still in the overridden state.
    """

    kind = "interruption"
    source = "D-GARA A3 (system resource)"

    def __init__(self, name: str = "low_battery", level: int = 5,
                 scope: str = "step", at_step: int | None = DEFAULT_STEP):
        super().__init__(name)
        self.level = int(level)
        self.scope = scope
        self.at_step = at_step

    def _apply(self, adb, context: dict) -> None:
        self._saved["battery"] = read_battery(adb)
        adb.shell("dumpsys", "battery", "unplug")
        adb.shell("dumpsys", "battery", "set", "level", str(self.level))

    def _revert(self, adb) -> None:
        adb.shell("dumpsys", "battery", "reset")

    def fingerprint(self, adb) -> dict:
        return {"battery": read_battery(adb)}


class NotificationArm(Arm):
    """D-GARA's UX disruption, posted by ``cmd notification post``.

    Revert is best effort and the limit is worth stating: there is no
    ``cmd notification cancel``. The arm collapses the shade and clears
    com.android.shell, which drops every notification this arm posted, and
    the fingerprint counts the shell-posted notifications still standing so a
    failed revert shows up in the log instead of leaking into the next arm.
    """

    kind = "interruption"
    scope = "step"

    def __init__(self, name: str, title: str, body: str,
                 tag: str = "guiexp_perturb", at_step: int | None = DEFAULT_STEP,
                 stand_in: bool = False, source: str = "D-GARA A3 (UX disruption)"):
        super().__init__(name)
        self.title = title
        self.body = body
        self.tag = tag
        self.at_step = at_step
        self.stand_in = stand_in
        self.source = source

    def _apply(self, adb, context: dict) -> None:
        adb.shell("cmd", "notification", "post", "-S", "bigtext",
                  "-t", self.title, self.tag, self.body)
        adb.shell("cmd", "statusbar", "expand-notifications", check=False)

    def _revert(self, adb) -> None:
        adb.shell("cmd", "statusbar", "collapse", check=False)
        adb.shell("pm", "clear", "com.android.shell", check=False)

    def fingerprint(self, adb) -> dict:
        return {"shell_notifications": count_shell_notifications(adb)}


class PermissionArm(Arm):
    """D-GARA's permission-control interruption, the largest category there
    at 42.8 percent of injected anomalies.

    No APK: the arm revokes one runtime permission that
    android_env.STARTUP_GRANTS pre-grants, so the dialog the app raises is
    the dialog a cold AVD would have raised on its first episode. Revert
    re-grants it, which is byte for byte the startup-grant state.

    Families whose app has no pre-granted runtime permission are skipped and
    recorded as not applicable, never as a pass.
    """

    kind = "interruption"
    scope = "session"
    source = "D-GARA A3 (permission control)"

    def __init__(self, name: str = "permission_dialog",
                 targets: dict[str, tuple[str, str]] | None = None):
        super().__init__(name)
        self.targets = targets if targets is not None else PERMISSION_TARGETS

    def _target(self, context: dict) -> tuple[str, str] | None:
        return self.targets.get(context.get("family") or "")

    def _skip_reason(self, context: dict) -> str | None:
        if self._target(context) is None:
            return (f"no pre-granted runtime permission recorded for family "
                    f"{context.get('family')!r}")
        return None

    def _apply(self, adb, context: dict) -> None:
        package, permission = self._target(context)
        self._saved["target"] = (package, permission)
        adb.shell("pm", "revoke", package, permission)
        adb.shell("am", "force-stop", package, check=False)

    def _revert(self, adb) -> None:
        target = self._saved.get("target")
        if not target:
            return
        package, permission = target
        adb.shell("pm", "grant", package, permission, check=False)

    def fingerprint(self, adb) -> dict:
        granted = {}
        for family, (package, permission) in sorted(self.targets.items()):
            out = adb.shell("dumpsys", "package", package, check=False) or ""
            match = re.search(re.escape(permission) + r"[^\n]*granted=(true|false)", out)
            granted[f"{package}:{permission.rsplit('.', 1)[-1]}"] = (
                match.group(1) == "true" if match else None
            )
        return {"permissions": granted}


# -- registry ---------------------------------------------------------------

ARMS: tuple[Arm, ...] = (
    CleanArm("clean"),
    SettingsArm("font_large", "system", "font_scale", "1.3",
                source="B-MoCA A8 (font size)"),
    SettingsArm("font_small", "system", "font_scale", "0.85",
                source="B-MoCA A8 (font size)"),
    DensityArm("density_small", 0.85),
    LocaleArm("locale_fr", "fr-FR"),
    ThemeArm("dark_theme"),
    NotificationArm("notification", title="Message from Alex",
                    body="Are you still coming tonight?"),
    BatteryArm("low_battery"),
    PermissionArm("permission_dialog"),
    NotificationArm("update_prompt", title="System update available",
                    body="Version 15.1 is ready to install. Restart now?",
                    tag="guiexp_update", stand_in=True,
                    source="D-GARA A3 (UX disruption), stand-in for the APK dialog"),
)

ARMS_BY_NAME: dict[str, Arm] = {arm.name: arm for arm in ARMS}

# Arms that leave the device configuration changed for a whole block of
# replays, versus arms that fire inside one replay.
SESSION_ARMS = tuple(a.name for a in ARMS if a.scope == "session")
STEP_ARMS = tuple(a.name for a in ARMS if a.scope == "step")


def select_arms(names: Iterable[str] | None) -> list[Arm]:
    """Arms by name, in registry order, with a useful error on a typo."""
    if not names:
        return list(ARMS)
    wanted = list(names)
    unknown = [n for n in wanted if n not in ARMS_BY_NAME]
    if unknown:
        raise ValueError(f"unknown arm(s) {unknown}; known: {sorted(ARMS_BY_NAME)}")
    return [arm for arm in ARMS if arm.name in set(wanted)]


# -- device fingerprint -----------------------------------------------------


def device_fingerprint(adb) -> dict:
    """Everything any arm can move, probed off the device in one go.

    Recorded before and after every arm so the run log says what the device
    was, not what we asked it to be. A clean-arm fingerprint that differs
    from the previous clean-arm fingerprint is a failed revert.
    """
    return {
        "system.font_scale": settings_get(adb, "system", "font_scale"),
        "system.system_locales": settings_get(adb, "system", "system_locales"),
        "secure.ui_night_mode": read_night_mode(adb),
        "wm_density": read_density(adb),
        "battery": read_battery(adb),
        "shell_notifications": count_shell_notifications(adb),
    }


def fingerprint_hash(fingerprint: dict) -> str:
    """Short stable digest of a fingerprint, for eyeballing a run log."""
    blob = json.dumps(fingerprint, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# -- session and step injection --------------------------------------------


@contextmanager
def arm_session(arm: Arm, adb, context: dict | None = None):
    """Apply a session arm, guarantee revert, yield the applied fingerprint.

    Revert runs even when the body raises, which is the whole point: a
    replay that dies mid-program must not leave the next family running at a
    1.3 font scale.
    """
    before = device_fingerprint(adb)
    applied: dict = {}
    try:
        applied = arm.apply(adb, context or {})
        yield {"before": before, "applied": applied, "skipped": arm.skipped_reason}
    finally:
        after = arm.revert(adb)
        arm.last_revert_fingerprint = after


class StepInjector:
    """Fires a step arm after N executed actions, once per replay.

    The probe's tracing device calls :meth:`after_action` with the running
    action count. The injector holds no device state of its own; the arm does
    the applying and the reverting.
    """

    def __init__(self, arm: Arm | None, adb, context: dict | None = None,
                 at_step: int | None = None):
        self.arm = arm
        self.adb = adb
        self.context = context or {}
        self.at_step = at_step if at_step is not None else (arm.at_step if arm else None)
        self.fired_at: int | None = None
        self.fingerprint: dict | None = None
        self.error: str | None = None

    @property
    def armed(self) -> bool:
        return self.arm is not None and self.at_step is not None and self.fired_at is None

    def after_action(self, actions_done: int) -> None:
        if not self.armed or actions_done < int(self.at_step):
            return
        self.fired_at = actions_done
        try:
            self.fingerprint = self.arm.apply(self.adb, self.context)
        except Exception as exc:  # noqa: BLE001 - an injector failure is data
            self.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"

    def force_fire(self) -> None:
        """Fire now even if the program never got as far as ``at_step``.

        A program that dies on its first locator would otherwise never see
        the interruption, and the arm would silently degrade into the clean
        arm. The probe calls this after a short replay so the run log can say
        the interruption did land, just later than scheduled.
        """
        if self.armed:
            self.after_action(int(self.at_step))

    def revert(self) -> dict | None:
        if self.arm is None or self.fired_at is None:
            return None
        return self.arm.revert(self.adb)

    def record(self) -> dict:
        return {
            "arm": self.arm.name if self.arm else None,
            "at_step": self.at_step,
            "fired_at": self.fired_at,
            "fired": self.fired_at is not None,
            "error": self.error,
            "fingerprint": self.fingerprint,
        }


def main(argv: list[str] | None = None) -> int:
    """Print the arm table and, with --fingerprint, the live device state."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fingerprint", action="store_true",
                        help="probe the attached device and print its fingerprint")
    parser.add_argument("--serial", default=None)
    args = parser.parse_args(argv)

    for arm in ARMS:
        d = arm.describe()
        tag = " (stand-in)" if d["stand_in"] else ""
        at = f" @step{d['at_step']}" if d["at_step"] is not None else ""
        print(f"{d['name']:<20} {d['kind']:<12} {d['scope']}{at}{tag}  {d['source']}")
    if args.fingerprint:
        adb = Adb(serial=args.serial)
        fp = device_fingerprint(adb)
        print(json.dumps(fp, indent=1, default=str))
        print(f"fingerprint {fingerprint_hash(fp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

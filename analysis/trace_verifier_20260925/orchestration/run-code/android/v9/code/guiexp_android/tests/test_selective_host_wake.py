"""Focused tests for the active current-state host guard."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from guiexp_android import selective_budget as sb


def _runner(outputs, failures=()):
    failures = {tuple(command) for command in failures}

    def run(command, **_kwargs):
        key = tuple(command)
        if key in failures:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=outputs.get(key, ""), stderr="")

    return run


def _ioreg(*, wake_type="UserActivity Assertion", lid="No", hibernate="00000000", marker="No"):
    return f'''+-o IOPMrootDomain {{
  "IOHibernateState" = <{hibernate}>
  "IOPMUserTriggeredFullWake" = {marker}
  "Wake Type" = "{wake_type}"
  "AppleClamshellState" = {lid}
}}'''


def _systemstate(*, capabilities="CPU Graphics Audio Network", power_state=4):
    return (
        f"Current System Capabilities are: {capabilities}\n"
        f"Current Power State: {power_state}\n"
    )


def _event(event, detail=None, timestamp="2026-09-16 00:03:58 +0800"):
    detail = detail or event
    return f"{timestamp} {event:<18} {detail}\n"


def _outputs(*, ioreg=None, systemstate=None, power_log=None, pmset="System-wide power settings:\n"):
    return {
        ("ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"): _ioreg() if ioreg is None else ioreg,
        ("pmset", "-g"): pmset,
        ("pmset", "-g", "systemstate"): _systemstate() if systemstate is None else systemstate,
        ("pmset", "-g", "log"): power_log if power_log is not None else _event("Wake", "DarkWake to FullWake from Deep Idle"),
    }


def _read(outputs):
    return sb.read_host_state(runner=_runner(outputs), now=lambda: 1234.0)


def test_accepts_current_full_wake_when_trigger_marker_is_no():
    state = _read(_outputs())
    assert state["ready"]
    assert state["normal_full_wake"]
    assert state["lid_open"]
    assert state["current_power_state"] == 4
    assert state["current_system_capabilities"] == ["audio", "cpu", "graphics", "network"]


def test_existing_active_host_fixture_replays_with_current_state_evidence():
    from guiexp_android.tests.test_selective_budget import good_ioreg, good_power_log

    state = _read(_outputs(ioreg=good_ioreg(), power_log=good_power_log()))
    assert state["ready"]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("systemstate", "current power state unavailable"),
        ("log", "power event unavailable"),
        ("lid", "lid state unavailable"),
        ("hibernate", "host sleeping"),
        ("capabilities", "current system capabilities incomplete"),
    ],
)
def test_missing_or_invalid_current_evidence_fails_closed(kind, expected):
    kwargs = {}
    if kind == "systemstate":
        kwargs["systemstate"] = ""
    elif kind == "log":
        kwargs["power_log"] = ""
    elif kind == "lid":
        kwargs["ioreg"] = _ioreg().replace('  "AppleClamshellState" = No\n', "")
    elif kind == "hibernate":
        kwargs["ioreg"] = _ioreg(hibernate="00000001")
    else:
        kwargs["systemstate"] = _systemstate(capabilities="CPU Network")
    state = _read(_outputs(**kwargs))
    assert not state["ready"]
    assert expected in state["reason"]


@pytest.mark.parametrize("event", ["DarkWake", "Sleep"])
def test_darkwake_or_sleep_as_latest_standalone_event_fails_closed(event):
    state = _read(_outputs(power_log=_event(event)))
    assert not state["ready"]
    assert event in state["reason"]


def test_wake_requests_is_not_a_standalone_wake_event():
    state = _read(_outputs(power_log=_event("Wake Requests", "[maintenance wake request]")))
    assert not state["ready"]
    assert "power event unavailable" in state["reason"]


def test_wake_requests_does_not_hide_a_later_standalone_wake():
    power_log = _event("Wake", "Wake from Deep Idle") + _event(
        "Wake Requests", "[future maintenance wake request]", "2026-09-16 00:04:00 +0800"
    )
    state = _read(_outputs(power_log=power_log))
    assert state["ready"]
    assert state["latest_power_event"]["event"].lower() == "wake"


def test_conflicting_current_power_state_fails_closed():
    state = _read(_outputs(systemstate=_systemstate() + "Current Power State: 2\n"))
    assert not state["ready"]
    assert "conflicting current power state" in state["reason"]


def test_conflicting_current_capabilities_fails_closed():
    systemstate = _systemstate() + "Current System Capabilities are: CPU\n"
    state = _read(_outputs(systemstate=systemstate))
    assert not state["ready"]
    assert "conflicting current system capabilities" in state["reason"]


def test_darkwake_marker_conflicts_with_full_wake_evidence():
    state = _read(_outputs(ioreg=_ioreg(wake_type="DarkWake")))
    assert not state["ready"]
    assert "DarkWake" in state["reason"]

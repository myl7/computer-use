"""v2 capture checks against the raw uiautomator fixtures."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_markor_label_probe_v2 as probe


FIXTURE_DIR = Path(
    "/Users/myl/app/computer-use/experimental-results/guiexp_android/"
    "selective_20260915/markor_label_probe_v1/xml"
)
LAUNCHER_ACTIVITY = (
    "com.google.android.apps.nexuslauncher/"
    "com.google.android.apps.nexuslauncher.NexusLauncherActivity"
)
MARKOR_ACTIVITY = "net.gsantner.markor/net.gsantner.markor.activity.MainActivity"


def _raw(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


class CaptureRun:
    def __init__(
        self,
        out: Path,
        before: bytes,
        after: bytes,
        activity: str,
        activity_after: str | None = None,
    ):
        self.out = out
        self.before = before
        self.after = after
        self.activity = activity
        self.activity_after = activity_after or activity
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.ax_count = 0

    def call(self, label: str, *args: str, check: bool = True):
        del check
        self.calls.append((label, args))
        if args[:3] == ("exec-out", "uiautomator", "dump"):
            self.ax_count += 1
            stdout = self.before if self.ax_count == 1 else self.after
        elif args[:3] == ("exec-out", "screencap", "-p"):
            stdout = probe.PNG_MAGIC + b"fixture"
        elif args[:3] == ("shell", "dumpsys", "window"):
            stdout = f"mCurrentFocus=Window{{u0 {self.activity_after}}}\n".encode()
        else:
            raise AssertionError(f"unexpected capture command: {args}")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")


def test_fragment_parser_accepts_real_xml_trailer_and_preserves_raw_bytes():
    raw = _raw("initial.xml")
    fragment, root = probe._hierarchy_fragment(raw, "initial")
    assert raw.endswith(b"UI hierchary dumped to: /dev/tty\n")
    assert fragment.endswith("</hierarchy>")
    assert root.tag == "hierarchy"
    assert len(fragment.encode()) < len(raw)


@pytest.mark.parametrize(
    "name,activity",
    [
        ("initial.xml", LAUNCHER_ACTIVITY),
        ("before_swipe_01.xml", LAUNCHER_ACTIVITY),
    ],
)
def test_initial_capture_allows_launcher_or_android_crash_overlay(
    tmp_path, name, activity
):
    raw = _raw(name)
    runner = CaptureRun(tmp_path, raw, raw, activity)
    xml, metadata = probe._capture_coherent(
        runner, "initial", activity, expected_package=None
    )
    assert xml == raw.decode("utf-8", "replace")
    assert metadata["package"] == activity.split("/", 1)[0]
    assert (tmp_path / "xml" / "initial_before.xml").read_bytes() == raw
    assert (tmp_path / "xml" / "initial_after.xml").read_bytes() == raw
    assert [label for label, _ in runner.calls] == [
        "initial_ax_before",
        "initial_screenshot",
        "initial_ax_after",
        "initial_focus_after",
    ]


def test_markor_capture_requires_unchanged_normalized_hierarchy(tmp_path):
    runner = CaptureRun(
        tmp_path,
        _raw("initial.xml"),
        _raw("after_open.xml"),
        MARKOR_ACTIVITY,
    )
    with pytest.raises(probe.ProbeStop, match="hierarchy changed"):
        probe._capture_coherent(runner, "after_open", MARKOR_ACTIVITY)
    assert [label for label, _ in runner.calls] == [
        "after_open_ax_before",
        "after_open_screenshot",
        "after_open_ax_after",
    ]
    assert (tmp_path / "xml" / "after_open_before.xml").read_bytes() == _raw(
        "initial.xml"
    )
    assert (tmp_path / "xml" / "after_open_after.xml").read_bytes() == _raw(
        "after_open.xml"
    )


def test_markor_capture_keeps_both_raw_views_when_stable(tmp_path):
    raw = _raw("after_open.xml")
    runner = CaptureRun(tmp_path, raw, raw, MARKOR_ACTIVITY)
    xml, metadata = probe._capture_coherent(runner, "after_open", MARKOR_ACTIVITY)
    assert xml == raw.decode("utf-8", "replace")
    assert metadata["xml_before_sha256"] == metadata["xml_after_sha256"]
    assert metadata["activity_after"] == MARKOR_ACTIVITY
    assert (tmp_path / "xml" / "after_open.xml").read_bytes() == raw


def test_markor_capture_stops_if_foreground_changes_after_png(tmp_path):
    raw = _raw("after_open.xml")
    runner = CaptureRun(
        tmp_path,
        raw,
        raw,
        MARKOR_ACTIVITY,
        activity_after=LAUNCHER_ACTIVITY,
    )
    with pytest.raises(probe.ProbeStop, match="foreground changed"):
        probe._capture_coherent(runner, "after_open", MARKOR_ACTIVITY)

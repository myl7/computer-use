"""One OSWorld docker container wrapped in the guiexp reset/step contract.

The guest-side server is OSWorld's own desktop_env/server/main.py (see
docs/osworld-port-log-2026-09.md): /screenshot returns the X11 screen as PNG,
/accessibility returns the AT-SPI tree as XML (the same XML OSWorld's mm_agents
consume), /execute runs a command in the guest (pyautogui lives there), /file
downloads a file, /setup/launch starts an app. This module adds:

  * container lifecycle -- boot ``osworld-guiexp:arm64`` when the named
    container is not running; ``docker stop`` only for containers this process
    started (the emulator discipline of guiexp_android, docker edition);
  * deterministic reset -- kill LibreOffice, restore the PRISTINE profile
    (the image parks one at ~/.config/libreoffice-pristine; this is the
    snapshot-revert of this environment: it also wipes Document Recovery,
    recent files and the lock file), clean the Desktop, launch the family's
    app fresh;
  * the numbered element list -- OSWorld's own filter
    (mm_agents/accessibility_tree_wrap/heuristic_retrieve.judge_node, ported
    verbatim below) over the AT XML; index = position in THIS filtered list,
    and the action space addresses exactly those indexes;
  * per-step observations in the guiexp shape (screenshot_b64, ax_tree_text,
    url = the ACTIVE WINDOW TITLE, goal_text, last_action[_error]) and reward
    = the family checker over the file pulled out of the guest.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import requests

from . import families
from .actions import ActionError, is_done, parse_action

# Container identity is env-overridable so the L5 server lane (cs11369a,
# x86_64 rebuild of the same image, container guiexp-osworld-l5) runs the
# SAME harness against the SAME image content without code forks. Defaults
# keep the L1 Mac lane (osworld-guiexp-1 / osworld-guiexp:arm64 :5901).
CONTAINER_NAME = os.environ.get("OSWORLD_CONTAINER", "osworld-guiexp-1")
CONTAINER_PORT = 5000
HOST_PORT = int(os.environ.get("OSWORLD_HOST_PORT", "5901"))
IMAGE = os.environ.get("OSWORLD_IMAGE", "osworld-guiexp:arm64")
SERVER = os.environ.get("OSWORLD_SERVER", f"http://127.0.0.1:{HOST_PORT}")

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = REPO_ROOT / "experimental-results" / "guiexp_osworld"

SETTLE_S = 2.0           # parity with the Android arm's per-action settle
APP_WAIT_S = 25.0        # LibreOffice cold start on this image is ~4-8 s
HTTP_TIMEOUT = 90


# ------------------------------------------------------------ container life


def container_running() -> bool:
    out = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{CONTAINER_NAME}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30,
    )
    return CONTAINER_NAME in out.stdout.split()


def start_container() -> None:
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER_NAME,
         "-p", f"127.0.0.1:{HOST_PORT}:{CONTAINER_PORT}",
         "--shm-size=512m", "--memory=4g", IMAGE],
        check=True, capture_output=True, timeout=120,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if requests.get(f"{SERVER}/platform", timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(2.0)
    raise TimeoutError(f"{CONTAINER_NAME} did not come up within 60 s")


def stop_container() -> None:
    subprocess.run(["docker", "stop", CONTAINER_NAME], capture_output=True, timeout=60)
    subprocess.run(["docker", "rm", CONTAINER_NAME], capture_output=True, timeout=60)


def ensure_container() -> bool:
    """Start the guest if needed; True iff THIS call started it."""
    if container_running():
        return False
    start_container()
    return True


# --------------------------------------------------------------- guest helpers


def _http_get(path: str, **kwargs) -> requests.Response:
    return requests.get(SERVER + path, timeout=HTTP_TIMEOUT, **kwargs)


def _http_post_json(path: str, payload: dict) -> dict:
    response = requests.post(SERVER + path, json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    if response.headers.get("Content-Type", "").startswith("application/json"):
        return response.json()
    return {"status": "success", "output": response.text}


def guest_execute(command: list[str]) -> dict:
    return _http_post_json("/execute", {"command": command, "shell": False})


def guest_bash(script: str, timeout: int = 60) -> tuple[int, str]:
    """One bash script in the guest; returns (returncode, output)."""
    payload = {"script": script, "timeout": timeout}
    response = requests.post(SERVER + "/run_bash_script", json=payload, timeout=timeout + 30)
    response.raise_for_status()
    data = response.json()
    return int(data.get("returncode", -1)), (data.get("output") or "")


def guest_python(code: str) -> dict:
    """Run one python snippet in the guest (pyautogui available)."""
    return _http_post_json("/run_python", {"code": code})


def get_screenshot() -> bytes:
    response = _http_get("/screenshot")
    response.raise_for_status()
    return response.content


def get_accessibility_xml() -> str:
    response = _http_get("/accessibility")
    response.raise_for_status()
    return response.json()["AT"]


def guest_file_exists(path: str) -> bool:
    code, out = guest_bash(f"test -f {path!r} && echo FOUND || echo MISSING")
    return "FOUND" in out


def get_guest_file(path: str) -> bytes | None:
    response = requests.post(SERVER + "/file", data={"file_path": path}, timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        return None
    return response.content


# ----------------------------------------------- AT XML -> element records


STATE_NS = "https://accessibility.ubuntu.example.org/ns/state"
ATTR_NS = "https://accessibility.ubuntu.example.org/ns/attributes"
COMPONENT_NS = "https://accessibility.ubuntu.example.org/ns/component"
VALUE_NS = "https://accessibility.ubuntu.example.org/ns/value"


def _coord(node: ET.Element, key: str) -> tuple[int, int]:
    raw = node.get(f"{{{COMPONENT_NS}}}{key}", "(-1, -1)")
    try:
        return eval(raw, {"__builtins__": {}}, {})  # noqa: S307 - server emits "(x, y)"
    except Exception:  # noqa: BLE001 - malformed extent: treat as offscreen
        return (-1, -1)


CLICKABLE_ROLES = {
    "push-button", "toggle-button", "menu-item", "check-menu-item",
    "radio-menu-item", "menu", "menu-bar", "check-box", "radio-button",
    "combo-box", "list-item", "tree-item", "tree", "page-tab", "table-cell",
    "table-column-header", "table-row-header", "icon", "link", "button",
    "scroll-bar", "spin-button", "thumb", "dimension",
}
EDITABLE_ROLES = {"text", "entry", "paragraph", "document-text", "document",
                  "document-spreadsheet", "document-presentation", "terminal"}
# Roles whose content a click+ctrl+a selects for replacement (form fields).
# Deliberately NOT table-cell (whole sheet) or paragraph/document (whole doc).
_TEXTFIELD_ROLES = {"text", "entry", "searchbox", "textfield", "textbox",
                    "spin-button"}


def judge_node(node: ET.Element) -> bool:
    """OSWorld's heuristic_retrieve.judge_node, ported verbatim (kept roles,
    showing+visible, enabled/editable/expandable/checkable, non-empty name or
    text, onscreen extent)."""
    keeps = (
        node.tag.startswith("document")
        or node.tag.endswith("item")
        or node.tag.endswith("button")
        or node.tag.endswith("heading")
        or node.tag.endswith("label")
        or node.tag.endswith("scrollbar")
        or node.tag.endswith("searchbox")
        or node.tag.endswith("textbox")
        or node.tag.endswith("link")
        or node.tag.endswith("tabelement")
        or node.tag.endswith("textfield")
        or node.tag.endswith("textarea")
        or node.tag.endswith("menu")
        or node.tag in {
            "alert", "canvas", "check-box", "combo-box", "entry", "icon",
            "image", "paragraph", "scroll-bar", "section", "slider", "static",
            "table-cell", "terminal", "text", "netuiribbontab", "start",
            "trayclockwclass", "traydummysearchcontrol", "uiimage",
            "uiproperty", "uiribboncommandbar",
        }
    )
    keeps = keeps and (
        node.get(f"{{{STATE_NS}}}showing", "false") == "true"
        and node.get(f"{{{STATE_NS}}}visible", "false") == "true"
    ) and (
        node.get(f"{{{STATE_NS}}}enabled", "false") == "true"
        or node.get(f"{{{STATE_NS}}}editable", "false") == "true"
        or node.get(f"{{{STATE_NS}}}expandable", "false") == "true"
        or node.get(f"{{{STATE_NS}}}checkable", "false") == "true"
    ) and (
        node.get("name", "") != ""
        or (node.text is not None and len(node.text) > 0)
    )
    (x, y) = _coord(node, "screencoord")
    (w, h) = _coord(node, "size")
    return keeps and x >= 0 and y >= 0 and w > 0 and h > 0


def element_records(at_xml: str) -> list[dict]:
    """The numbered element list: every node that passes OSWorld's filter, in
    document order; ``index`` is the position in this list and exactly what
    the action space and device.find address."""
    root = ET.fromstring(at_xml)
    records = []
    for node in root.iter():
        if not judge_node(node):
            continue
        (x, y) = _coord(node, "screencoord")
        (w, h) = _coord(node, "size")
        records.append({
            "index": len(records),
            "role": node.tag,
            "name": node.get("name", ""),
            "text": (node.text or "").replace("\ufffc", "").strip(),
            "description": node.get(f"{{{ATTR_NS}}}description", ""),
            "value": node.get(f"{{{VALUE_NS}}}value", ""),
            "clickable": node.tag in CLICKABLE_ROLES,
            "editable": (
                node.get(f"{{{STATE_NS}}}editable", "false") == "true"
                or node.tag in EDITABLE_ROLES
            ),
            "selected": node.get(f"{{{STATE_NS}}}selected", "false") == "true",
            "focused": node.get(f"{{{STATE_NS}}}focused", "false") == "true",
            "checked": node.get(f"{{{STATE_NS}}}checked", "false") == "true",
            "x": x, "y": y, "w": w, "h": h,
            "cx": x + w // 2, "cy": y + h // 2,
        })
    return records


def element_list_text(records: list[dict], max_lines: int = 400) -> str:
    """The M3A-text-variant numbered list (one compact JSON-ish line each).

    Table cells (the majority on a Calc screen) get the shortest lines: role
    aliased to "cell", no flags a cell does not vary on -- keeping a Calc
    observation within ~1.5x of the Android arm's per-step observation size
    (calibrated on t12_grid discover__ContactsAddContact__s1: ~7k tokens/step).
    """
    lines = []
    for record in records[:max_lines]:
        index, role, name = record["index"], record["role"], record["name"]
        if role == "table-cell":
            parts = [f'"index": {index}', '"role": "cell"']
            if name:
                parts.append(f'"name": "{name}"')
            if record["text"]:
                parts.append(f'"text": "{record["text"][:80].replace(chr(34), chr(39))}"')
            if record["selected"]:
                parts.append('"selected": true')
            lines.append(f"Element {index}: {{" + ", ".join(parts) + "}")
            continue
        parts = [f'"index": {index}', f'"role": "{role}"']
        if name:
            parts.append(f'"name": "{name}"')
        if record["text"]:
            parts.append(f'"text": "{record["text"][:120].replace(chr(34), chr(39))}"')
        if record["description"]:
            parts.append(f'"description": "{record["description"][:80]}"')
        parts.append(f'"clickable": {str(record["clickable"]).lower()}')
        if record["editable"]:
            parts.append('"editable": true')
        if record["selected"]:
            parts.append('"selected": true')
        if record["focused"]:
            parts.append('"focused": true')
        lines.append(f"Element {index}: {{" + ", ".join(parts) + "}")
    if len(records) > max_lines:
        lines.append(f"... {len(records) - max_lines} more elements not listed "
                     "(their indexes still exist)")
    return "\n".join(lines)


_TOPLEVEL_ROLES = ("frame", "dialog", "file-chooser", "alert", "window")


def active_window_title(at_xml: str) -> str:
    """The where-am-I string: the ACTIVE toplevel's title, and when a modal
    (dialog/file-chooser) is up, 'frame | dialog' so the agent knows."""
    root = ET.fromstring(at_xml)
    frame = modal = active = ""
    for node in root.iter():
        if node.tag not in _TOPLEVEL_ROLES:
            continue
        name = node.get("name", "")
        if node.get(f"{{{STATE_NS}}}active", "false") == "true":
            active = name
        if node.tag == "frame" and not frame:
            frame = name
        if node.tag in ("dialog", "file-chooser", "alert") and name and not modal:
            modal = name
    if modal:
        return f"{frame or active} | {modal}"
    return active or frame or "desktop"


# ------------------------------------------------------------------- the env


@dataclass(frozen=True)
class OSWorldTask:
    """One cell: family x condition x seed (the AndroidTask shape)."""

    family: str
    condition: str
    seed: int
    params: dict

    @property
    def task_id(self) -> str:
        return f"{self.family}__{self.condition}__s{self.seed}"


def get_task(family: str, condition: str, seed: int) -> OSWorldTask:
    if family not in families.FAMILIES:
        raise ValueError(f"unknown family {family!r}")
    return OSWorldTask(family=family, condition=condition, seed=seed,
                       params=families.instance_params(family, seed))


def task_goal(task: OSWorldTask) -> str:
    return "" if task.condition == "floor" else families.goal_text(
        task.family, task.seed, task.params)


RESET_SCRIPT = (
    "pkill -9 -f soffice || true; sleep 1; "
    "rm -rf /home/user/.config/libreoffice; "
    "cp -a /home/user/.config/libreoffice-pristine /home/user/.config/libreoffice; "
    "rm -f /home/user/Desktop/* 2>/dev/null || true; "
    "echo RESET_OK"
)


class OSWorldEnv:
    """reset(task) -> observation dict; step(action_text) -> (obs, done, reward).

    ``reward`` is the family's own checker over the guest file; the episode
    auto-terminates at reward >= 1.0 exactly like both existing arms.
    """

    def __init__(self, start_if_needed: bool = True, settle_s: float = SETTLE_S):
        self.started_by_us = False
        if not container_running():
            if not start_if_needed:
                raise RuntimeError(
                    f"no container {CONTAINER_NAME}; start it or pass start_if_needed=True")
            start_container()
            self.started_by_us = True
        self.settle_s = settle_s
        self.task: OSWorldTask | None = None

    # -- lifecycle ----------------------------------------------------------

    def reset(self, task: OSWorldTask) -> dict:
        code, out = guest_bash(RESET_SCRIPT, timeout=60)
        if "RESET_OK" not in out:
            raise RuntimeError(f"guest reset failed: rc={code} out={out[:200]}")
        self.task = task
        # floor never launches an app: the probe measures harness overhead on
        # the bare desktop, so the observation carries no task-side tokens.
        if task.family in families.APP_LAUNCH and task.condition != "floor":
            self._launch_app(task.family)
        time.sleep(1.0)
        return self._observe(task_goal(task))

    def observe(self, goal: str | None = None) -> dict:
        """One observation of the CURRENT screen without resetting (the repair
        loop resumes from wherever a failed program left the guest)."""
        if goal is None:
            goal = task_goal(self.task) if self.task else ""
        return self._observe(goal)

    def _launch_app(self, family: str) -> None:
        _http_post_json("/setup/launch",
                        {"command": families.APP_LAUNCH[family], "shell": False})
        needle = "Calc" if "calc" in families.APP_LAUNCH[family][-1] else "Writer"
        deadline = time.time() + APP_WAIT_S
        while time.time() < deadline:
            code, out = guest_bash("wmctrl -l", timeout=10)
            if needle in out:
                time.sleep(1.5)  # toolbar/grid a11y not up the instant wmctrl sees it
                return
            time.sleep(1.0)
        raise TimeoutError(f"{family}: LibreOffice window ({needle}) never appeared")

    def close(self) -> None:
        self.task = None

    def stop_container(self, force: bool = False) -> None:
        if self.started_by_us or force:
            stop_container()

    # -- observation --------------------------------------------------------

    def _observe(self, goal: str) -> dict:
        at_xml = get_accessibility_xml()
        records = element_records(at_xml)
        self._records = records  # the resolution table of the LAST observation
        return {
            "screenshot_b64": base64.b64encode(get_screenshot()).decode("ascii"),
            "ax_tree_text": element_list_text(records),
            # the "where am I" line: on this arm the ACTIVE WINDOW TITLE
            # (schema key kept from the web/Android arms)
            "url": active_window_title(at_xml),
            "goal_text": goal,
            "ax_elements": len(records),
        }

    def element_by_index(self, index: int) -> dict | None:
        """Resolve an index against a FRESH dump: the reactive loop acts right
        after observing (the screen is settled), and the program/device path
        interleaves its own dumps -- a cached list would resolve against a
        screen that no longer exists."""
        records = element_records(get_accessibility_xml())
        self._records = records
        if 0 <= index < len(records):
            return records[index]
        return None

    # -- acting and reward --------------------------------------------------

    def execute_action(self, action: dict) -> None:
        """Run one parsed action in the guest through pyautogui."""
        kind = action.get("action_type")
        if kind in ("click", "long_press"):
            element = self.element_by_index(int(action["index"]))
            if element is None:
                raise ValueError(f"click: no element with index {action['index']}")
            duration = 0.8 if kind == "long_press" else 0.0
            guest_python(
                "import pyautogui; pyautogui.click({x}, {y}, duration={d})".format(
                    x=element["cx"], y=element["cy"], d=duration))
        elif kind == "input_text":
            text = str(action.get("text", ""))
            index = action.get("index")
            script = ["import pyautogui, time"]
            role = None
            if index is not None:
                element = self.element_by_index(int(index))
                if element is None:
                    raise ValueError(f"input_text: no element with index {index}")
                role = element["role"]
                script.append(f"pyautogui.click({element['cx']}, {element['cy']})")
                script.append("time.sleep(0.4)")
                if role in _TEXTFIELD_ROLES:
                    # a form field: select its content so typing replaces it
                    script.append("pyautogui.hotkey('ctrl', 'a')")
                    script.append("time.sleep(0.1)")
                # table cells / paragraphs: a click already positions the
                # caret; ctrl+a there would select the WHOLE sheet/document.
            script.append(f"pyautogui.typewrite({json.dumps(text)}, interval=0.012)")
            if role == "table-cell":
                # commit the cell edit and move down (Calc semantics)
                script.append("time.sleep(0.15)")
                script.append("pyautogui.press('enter')")
            guest_python("\n".join(script))
        elif kind == "press":
            key = str(action.get("key", ""))
            guest_python(f"import pyautogui; pyautogui.press({json.dumps(key)})")
        elif kind == "hotkey":
            keys = [str(k) for k in action.get("keys", [])]
            if not keys:
                raise ValueError("hotkey: needs keys")
            call = ", ".join(json.dumps(k) for k in keys)
            guest_python(f"import pyautogui; pyautogui.hotkey({call})")
        elif kind == "scroll":
            direction = action.get("direction", "down")
            clicks = {"down": -1200, "up": 1200}.get(direction)
            if clicks is not None:
                guest_python(f"import pyautogui; pyautogui.scroll({clicks})")
            else:
                guest_python(
                    f"import pyautogui; pyautogui.hscroll("
                    f"{'-1200' if direction == 'left' else '1200'})")
        elif kind == "wait":
            time.sleep(1.0)
        elif kind == "open_app":
            app = str(action.get("app_name", "")).lower()
            if "calc" in app:
                _http_post_json("/setup/launch",
                                {"command": ["libreoffice", "--calc"], "shell": False})
            elif "writer" in app or "word" in app:
                _http_post_json("/setup/launch",
                                {"command": ["libreoffice", "--writer"], "shell": False})
            else:
                raise ValueError(f"open_app: unknown app {app!r}")
            time.sleep(3.0)
        else:
            raise ValueError(f"unknown action_type {kind!r}")
        time.sleep(self.settle_s)

    def step(self, action_text: str) -> tuple[dict, bool, float]:
        """Execute one action reply; return (obs, done, reward)."""
        if self.task is None:
            raise RuntimeError("call reset(task) before step()")

        error: str | None = None
        action = None
        try:
            action = parse_action(action_text)
        except ActionError as exc:
            error = f"{type(exc).__name__}: {exc}"

        done = False
        reward = self.reward()
        if action is not None and not is_done(action):
            try:
                self.execute_action(action)
                reward = self.reward()
            except Exception as exc:  # noqa: BLE001 - the failure shape is data
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        elif action is not None and is_done(action):
            done = True
        if reward >= 1.0:
            done = True

        obs = self._observe(task_goal(self.task))
        obs["last_action"] = action_text
        obs["last_action_error"] = error
        return obs, done, reward

    def reward(self) -> float:
        """1.0 iff the family checker passes over the saved guest file."""
        if self.task is None or self.task.condition == "floor":
            return 0.0
        path = f"{families.GUEST_DESKTOP}/{self.task.params['file_name']}"
        if not guest_file_exists(path):
            return 0.0
        file_bytes = get_guest_file(path)
        if file_bytes is None:
            return 0.0
        try:
            passed, _error = families.CHECKERS[self.task.family](
                self.task.params, file_bytes)
        except Exception:  # noqa: BLE001 - checker crash = not successful
            return 0.0
        return 1.0 if passed else 0.0

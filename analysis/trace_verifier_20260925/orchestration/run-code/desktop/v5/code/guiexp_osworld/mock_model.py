"""Offline stand-ins for the E2E mock test (zero LLM, real guest).

* ``MockOSWorld`` -- an openai-style client whose agent replies replay the
  golden program as a state machine over the observation's numbered element
  list (click cell -> type value -> ... -> save dialog -> status complete),
  one action per call. Builder calls return the golden program source (code)
  or a short doc; translator calls return a canned analysis+snippet.
* ``mock_extract_fields`` -- deterministic extraction of the binding from a
  deploy-style goal.

Usage is tests only; no network access anywhere.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace


# ------------------------------------------------------------- the agent script

def _parse_elements(ax_text: str) -> list[dict]:
    elements = []
    for line in ax_text.splitlines():
        m = re.match(r"Element (\d+): \{(.*)\}$", line.strip())
        if not m:
            continue
        fields = {}
        for part in re.findall(r'"(\w+)": ("[^"]*"|true|false|-?\d+)', m.group(2)):
            key, raw = part
            if raw == "true":
                fields[key] = True
            elif raw == "false":
                fields[key] = False
            elif re.fullmatch(r"-?\d+", raw):
                fields[key] = int(raw)
            else:
                fields[key] = raw.strip('"')
        fields["index"] = int(m.group(1))
        elements.append(fields)
    return elements


def _parse_goal(goal: str, family: str) -> dict | None:
    try:
        if family == "CalcTableSave":
            quoted = re.findall(r"'([^']+)'", goal)
            numbers = [int(n) for n in re.findall(r"\b(\d{3,4})\b", goal)]
            file_name = re.search(r"(\w+\.ods)", goal).group(1)
            if len(quoted) < 2 or len(numbers) < 4:
                return None
            return {
                "file_name": file_name,
                "header_a": quoted[0], "header_b": quoted[1],
                "val_a2": numbers[0], "val_b2": numbers[1],
                "val_a3": numbers[2], "val_b3": numbers[3],
            }
        quoted = re.findall(r"'([^']+)'", goal)
        file_name = re.search(r"(\w+\.odt)", goal).group(1)
        if len(quoted) < 2:
            return None
        return {"file_name": file_name, "title": quoted[0], "body": quoted[1]}
    except (AttributeError, IndexError):
        return None


class _ScriptedCompletions:
    def __init__(self):
        self.state: dict = {}

    def create(self, *, model=None, messages=None, **_kwargs):
        system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        user = ""
        ax_text = ""
        for message in reversed(messages or []):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        user += part.get("text", "") + "\n"
            else:
                user = str(content)
            break
        ax_match = re.search(r"Numbered list of UI elements.*?\n(.*)", user, re.S)
        if ax_match:
            ax_text = ax_match.group(1)

        if "operation document" in system and "reply with the document text" in system:
            return self._doc_reply(model)
        if "compile a recorded GUI automation" in system:
            return self._code_reply(model, user)
        if "make one recorded GUI action robust" in system:
            return self._translator_reply(model)
        if "extract structured parameters" in system:
            return self._extract_reply(model, user)
        return self._agent_reply(model, user, ax_text)

    # -- the agent state machine --------------------------------------------

    def _agent_reply(self, model, user, ax_text):
        if "Reply with exactly" in user:  # the floor probe: terminate at once
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='action: {"action_type": "status", "goal_status": "complete"}'))],
                usage=SimpleNamespace(prompt_tokens=800, completion_tokens=12,
                                      cost=0.00005),
                model=model or "mock")
        family = "CalcTableSave" if "Calc" in user else "WriterMemoSave"
        state = self.state
        state["step"] = state.get("step", 0)
        has_goal = user.count("'") >= 2 and bool(re.search(r"\w+\.od[st]", user)) \
            and "Request:" not in user
        binding = _parse_goal(user, family) if has_goal else None
        if binding is not None:
            state["binding"] = binding  # a goal on the wire starts a new episode
            state["step"] = 0
        else:
            binding = state.get("binding")
            if binding is None:
                raise RuntimeError("mock: no goal seen yet")
        elements = _parse_elements(ax_text) if ax_text else []

        def find_index(name=None, role=None, text_startswith=None):
            for element in elements:
                if name is not None and element.get("name") != name:
                    continue
                if role is not None and element.get("role") != role:
                    continue
                if text_startswith is not None and not str(element.get("text", "")).startswith(text_startswith):
                    continue
                return element["index"]
            return None

        def reply(action):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="action: " + json.dumps(action)))],
                usage=SimpleNamespace(prompt_tokens=800, completion_tokens=12,
                                      cost=0.00005),
                model=model or "mock")

        if family == "CalcTableSave":
            cells = (("A1", "header_a"), ("B1", "header_b"), ("A2", "val_a2"),
                     ("B2", "val_b2"), ("A3", "val_a3"), ("B3", "val_b3"))
            if state["step"] < len(cells) * 2:
                cell, field = cells[state["step"] // 2]
                if state["step"] % 2 == 0:
                    state["step"] += 1
                    return reply({"action_type": "click",
                                  "index": find_index(name=cell, role="cell")})
                state["step"] += 1
                return reply({"action_type": "input_text",
                              "text": str(binding[field]),
                              "index": find_index(name=cell, role="cell")})
            if state["step"] == len(cells) * 2:
                state["step"] += 1
                return reply({"action_type": "hotkey", "keys": ["ctrl", "s"]})
            if state["step"] == len(cells) * 2 + 1:
                state["step"] += 1
                return reply({"action_type": "click",
                              "index": find_index(name="Desktop", role="label")})
            if state["step"] == len(cells) * 2 + 2:
                state["step"] += 1
                return reply({"action_type": "input_text",
                              "text": binding["file_name"],
                              "index": find_index(role="text", text_startswith="Untitled")})
            state["step"] += 1
            return reply({"action_type": "press", "key": "enter"})
        else:
            if state["step"] == 0:
                state["step"] += 1
                return reply({"action_type": "input_text", "text": binding["title"]})
            if state["step"] == 1:
                state["step"] += 1
                return reply({"action_type": "press", "key": "enter"})
            if state["step"] == 2:
                state["step"] += 1
                return reply({"action_type": "press", "key": "enter"})
            if state["step"] == 3:
                state["step"] += 1
                return reply({"action_type": "input_text", "text": binding["body"]})
            if state["step"] == 4:
                state["step"] += 1
                return reply({"action_type": "hotkey", "keys": ["ctrl", "s"]})
            if state["step"] == 5:
                state["step"] += 1
                return reply({"action_type": "click",
                              "index": find_index(name="Desktop", role="label")})
            if state["step"] == 6:
                state["step"] += 1
                return reply({"action_type": "input_text",
                              "text": binding["file_name"],
                              "index": find_index(role="text", text_startswith="Untitled")})
            state["step"] += 1
            return reply({"action_type": "press", "key": "enter"})

    # -- builder / translator / extraction ------------------------------------

    GOLDEN = None  # filled by the test driver with the golden program source

    def _code_reply(self, model, user):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="```python\n" + (self.GOLDEN or "def program(device, binding):\n    return True\n") + "```"))],
            usage=SimpleNamespace(prompt_tokens=2500, completion_tokens=700,
                                  cost=0.00040),
            model=model or "mock")

    def _doc_reply(self, model):
        doc = ("1. In the app, create the content the goal describes.\n"
               "2. Save it with ctrl+s through the save dialog: pick Desktop,\n"
               "type the {file_name} into the Name field, confirm with Enter.\n")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=doc))],
            usage=SimpleNamespace(prompt_tokens=2500, completion_tokens=80,
                                  cost=0.00020),
            model=model or "mock")

    def _translator_reply(self, model):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="ANALYSIS: mock\n```python\nidx = device.find(name='A1', role='table-cell')\ndevice.click(index=idx)\n```"))],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=45,
                                  cost=0.00010),
            model=model or "mock")

    def _extract_reply(self, model, user):
        m = re.search(r"Request: (.+)", user, re.S)
        family = "CalcTableSave" if "Calc" in (m.group(1) if m else user) else "WriterMemoSave"
        fields = mock_extract_fields(m.group(1) if m else user, family)
        reply = "```json\n" + json.dumps(fields) + "\n```"
        prompt_tokens = 350
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=prompt_tokens,
                                  completion_tokens=max(1, len(reply) // 4),
                                  cost=0.00002),
            model=model or "mock")


def mock_extract_fields(goal: str, family: str) -> dict:
    return _parse_goal(goal.strip(), family)


class MockOSWorld:
    """Drop-in openai-style client for tests: deterministic, offline."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_ScriptedCompletions())

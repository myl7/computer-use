"""Unit tests for the GuiAgent reactive image-413 guard.

The agent sends every request normally (byte-identical to the unguarded
client); only when the channel rejects a request with HTTP 413 (image
payload over the limit) does it strip the CURRENT step's screenshot, retry
immediately once, and freeze the episode's image set -- every later
observation text-only, history images never removed, request prefix up to
the strip point byte-identical. The degenerate path (still 413 after the
current-step strip) strips the most recent HISTORY image, repeating, and is
logged loudly as prefix-breaking.

Mock images are plain bytes objects of controlled size, base64-encoded
exactly like the env sends them (no real PNGs, no network, no browser); the
413 is a canned exception with status_code 413. Plain unittest (asserts), so
it runs with the system python3; pytest runs it unchanged.
"""

from __future__ import annotations

import base64
import copy
import contextlib
import io
import json
import unittest
from types import SimpleNamespace

from guiexp.agent import GuiAgent

DATA_URL_PREFIX = "data:image/png;base64,"
AX_PREFIX = (
    "Accessibility tree of the current page (one line per "
    "interactive element: [bid] role 'name'):\n"
)


class Fake413(Exception):
    """Canned OpenRouter image-budget rejection."""

    status_code = 413

    def __init__(self, message="Error: 413 - Downloaded image content cannot exceed 30MB"):
        super().__init__(message)


class FakeServerError(Exception):
    """A non-image channel error; must NOT trigger the guard."""

    status_code = 500


class _ScriptedClient:
    """Records a deep copy of every create() call; scripted 413s by index."""

    def __init__(self, raise_413_at=(), other_errors=()):
        self.calls = []  # deep copies, so in-place strips stay observable
        self.raise_413_at = set(raise_413_at)
        self.other_errors = dict(other_errors)  # call index -> exception
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, model=None, messages=None, **_kwargs):
        idx = len(self.calls)
        self.calls.append(copy.deepcopy(messages))
        if idx in self.raise_413_at:
            raise Fake413()
        if idx in self.other_errors:
            raise self.other_errors[idx]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done()"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1),
        )


def _b64_image(n_bytes: int) -> str:
    """A mock screenshot: n_bytes of bytes, encoded as the env would send it."""
    return base64.b64encode(b"\x00" * n_bytes).decode()


def _obs(image_b64: str | None) -> dict:
    obs = {"url": "http://localhost:5001/map", "ax_tree_text": "[b1] button 'Layers'"}
    if image_b64 is not None:
        obs["screenshot_b64"] = image_b64
    return obs


def _user_content(img_b64: str, goal: str | None = None) -> list[dict]:
    """The observation content parts exactly as _observation_parts builds them."""
    text = (goal.rstrip("\n") + "\n\n") if goal else ""
    text += "Current URL: http://localhost:5001/map\n"
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": DATA_URL_PREFIX + img_b64}},
        {"type": "text", "text": AX_PREFIX + "[b1] button 'Layers'"},
    ]


def _image_urls(messages: list[dict]) -> list[str]:
    urls = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for part in msg["content"]:
            if isinstance(part, dict) and part.get("type") == "image_url":
                urls.append(part["image_url"]["url"])
    return urls


class TestImage413Guard(unittest.TestCase):
    def test_normal_episode_sends_untouched_requests(self):
        """No 413: every request is the full legacy build, nothing recorded."""
        client = _ScriptedClient()
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        usages = []
        for step in range(3):
            reply, usage = agent.act("goal text" if step == 0 else None, _obs(img))
            usages.append(usage)

        expected = []
        for step in range(3):
            req = [{"role": "system", "content": agent.system_prompt}]
            for s in range(step):  # completed pairs enter history after the call
                req.append({"role": "user", "content": _user_content(img, "goal text" if s == 0 else None)})
                req.append({"role": "assistant", "content": "done()"})
            req.append({"role": "user", "content": _user_content(img, "goal text" if step == 0 else None)})
            expected.append(req)
        self.assertEqual(client.calls, expected)  # deep equality
        for got, want in zip(client.calls, expected):
            self.assertEqual(json.dumps(got), json.dumps(want))  # byte-identical
        self.assertEqual(len(_image_urls(client.calls[-1])), 3)  # full history kept
        self.assertEqual(agent.image_413_events, [])
        self.assertFalse(agent._images_frozen)
        self.assertTrue(all("image_413_events" not in u for u in usages))

    def test_413_strips_current_image_and_freezes(self):
        """413 on step 2: one immediate retry without that screenshot, then
        every later step is text-only; earlier history images stay."""
        client = _ScriptedClient(raise_413_at=[2])  # step 2, first attempt
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        for step in range(4):
            reply, usage = agent.act(None, _obs(img))
            if step == 2:
                freeze_usage = usage  # the call whose first attempt got the 413

        self.assertEqual(len(client.calls), 5)  # steps 0,1 + step2 twice + step3
        attempt1, attempt2 = client.calls[2], client.calls[3]
        # Retry is the SAME request minus the current image part, text kept.
        self.assertEqual(len(_image_urls(attempt1)), 3)
        self.assertEqual(len(_image_urls(attempt2)), 2)
        self.assertEqual(_image_urls(attempt2), _image_urls(attempt1)[:2])
        self.assertEqual(
            [p for p in attempt2[-1]["content"] if p.get("type") != "image_url"],
            [p for p in attempt1[-1]["content"] if p.get("type") != "image_url"],
        )
        self.assertTrue(any("[b1] button 'Layers'" in p["text"] for p in attempt2[-1]["content"]))
        # Frozen: step 3 goes out text-only in a single call.
        self.assertEqual(len(client.calls[4]), len(attempt2) + 2)
        self.assertEqual(_image_urls(client.calls[4]), _image_urls(attempt2))
        self.assertTrue(all(p.get("type") != "image_url" for p in client.calls[4][-1]["content"]))
        self.assertTrue(agent._images_frozen)

        event = agent.image_413_events[0]
        self.assertEqual(event["trigger"], "http_413")
        self.assertEqual(event["source"], "current")
        self.assertFalse(event["degenerate"])
        self.assertTrue(event["frozen"])
        self.assertEqual(event["images_stripped"], 1)
        self.assertEqual(event["bytes_stripped"], len(DATA_URL_PREFIX) + len(img))
        self.assertEqual(event["text_only_turns_after_freeze"], 1)  # step 3
        self.assertTrue("image_413_events" in freeze_usage)
        self.assertTrue("image_413_events" not in usage)  # step 3 was clean
        # Runner seam: the usage events are the agent's dicts, so the stamp lands.
        for ev in freeze_usage["image_413_events"]:
            ev["step"] = 2
        self.assertEqual(agent.image_413_events[0]["step"], 2)

    def test_post_freeze_prefix_is_byte_identical(self):
        """Everything up to and including the stripped turn never changes."""
        client = _ScriptedClient(raise_413_at=[2])
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        for _ in range(4):
            agent.act(None, _obs(img))

        frozen_req, later_req = client.calls[3], client.calls[4]  # retry, next step
        prefix = later_req[: len(frozen_req)]
        self.assertEqual(prefix, frozen_req)  # same turns, same content
        self.assertEqual(json.dumps(prefix), json.dumps(frozen_req))  # byte-identical
        # The already-sent images are still there, untouched (no eviction).
        self.assertEqual(_image_urls(later_req), _image_urls(frozen_req))
        self.assertEqual(len(_image_urls(later_req)), 2)

    def test_degenerate_double_413_strips_history_image(self):
        """Still 413 after the current-step strip: the most recent HISTORY
        image goes, loudly, and prefix stability is broken as recorded."""
        stderr = io.StringIO()
        client = _ScriptedClient(raise_413_at=[1, 2])  # step 1: attempt 1 and 2
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        with contextlib.redirect_stderr(stderr):
            agent.act(None, _obs(img))  # step 0: fine
            reply, usage = agent.act(None, _obs(img))  # step 1: 413 twice

        self.assertEqual(len(client.calls), 4)  # ok, 413, 413 again, stripped history
        self.assertEqual(len(agent.image_413_events), 2)
        first, second = agent.image_413_events
        self.assertFalse(first["degenerate"])
        self.assertEqual(first["source"], "current")
        self.assertTrue(second["degenerate"])
        self.assertEqual(second["source"], "history")
        self.assertEqual(second["bytes_stripped"], len(DATA_URL_PREFIX) + len(img))
        # The history image (step 0's) is really gone from the last request.
        self.assertEqual(_image_urls(client.calls[3]), [])
        self.assertEqual(
            [p for msg in agent.history if msg["role"] == "user" for p in msg["content"]
             if isinstance(p, dict) and p.get("type") == "image_url"],
            [],
        )
        self.assertIn("WARNING", stderr.getvalue())
        self.assertIn("degenerate", stderr.getvalue())
        # Subsequent steps stay text-only.
        agent.act(None, _obs(img))
        self.assertEqual(_image_urls(client.calls[4]), [])
        self.assertEqual(first["text_only_turns_after_freeze"], 1)

    def test_413_is_not_sent_through_the_generic_retry_path(self):
        """Exactly one immediate retry on 413 (no backoff loop); a non-image
        error propagates untouched, and the message wording alone (no status
        code) still triggers the safety net."""
        # 413: exactly two calls for the affected step, then success.
        client = _ScriptedClient(raise_413_at=[0])
        agent = GuiAgent(model="m", client=client)
        reply, usage = agent.act(None, _obs(_b64_image(600_000)))
        self.assertEqual(reply, "done()")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(usage["image_413_events"]), 1)

        # A 500 is neither retried nor stripped here: it propagates as before.
        client2 = _ScriptedClient(other_errors={0: FakeServerError("Error: 500 - boom")})
        agent2 = GuiAgent(model="m", client=client2)
        with self.assertRaises(FakeServerError):
            agent2.act(None, _obs(_b64_image(600_000)))
        self.assertEqual(len(client2.calls), 1)
        self.assertEqual(agent2.image_413_events, [])

        # Safety net: "cannot exceed" wording without a status_code attribute.
        class _MessageOnly(Exception):
            pass

        client3 = _ScriptedClient()
        client3.other_errors = {0: _MessageOnly("Downloaded image content cannot exceed 30MB")}
        agent3 = GuiAgent(model="m", client=client3)
        agent3.act(None, _obs(_b64_image(600_000)))
        self.assertEqual(len(client3.calls), 2)
        self.assertEqual(agent3.image_413_events[0]["trigger"], "cannot_exceed_message")


if __name__ == "__main__":
    unittest.main()

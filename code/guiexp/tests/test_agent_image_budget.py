"""Unit tests for the GuiAgent per-request image-budget freeze guard.

Mock screenshots are plain bytes objects of controlled size, base64-encoded
exactly like the env sends them (no real PNGs, no network, no browser).
Exercised here: the under-budget no-op is byte-identical to the unguarded
build; the first screenshot that would cross the budget is omitted and the
image set freezes; after the freeze the request prefix (everything up to and
including the freeze turn) stays byte-identical across later requests and no
new image ever appears; and the GUIEXP_IMAGE_BUDGET_MB override works.

Plain unittest (asserts), so it runs with the system python3; pytest runs it
unchanged.
"""

from __future__ import annotations

import base64
import json
import os
import unittest
from types import SimpleNamespace

from guiexp.agent import GuiAgent, image_budget_bytes

MB = 1024 * 1024
DATA_URL_PREFIX = "data:image/png;base64,"


def _b64_image(n_bytes: int) -> str:
    """A mock screenshot: n_bytes of bytes, encoded as the env would send it."""
    return base64.b64encode(b"\x00" * n_bytes).decode()


def _obs(image_b64: str | None) -> dict:
    obs = {"url": "http://localhost:5001/map", "ax_tree_text": "[b1] button 'Layers'"}
    if image_b64 is not None:
        obs["screenshot_b64"] = image_b64
    return obs


class _CapturingClient:
    """Records every create() call's messages; deterministic stub reply."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, model=None, messages=None, **_kwargs):
        self.calls.append(messages)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done()"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1),
        )


def _image_urls(messages: list[dict]) -> list[str]:
    """Every image data URL in a request, in message order."""
    urls = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for part in msg["content"]:
            if isinstance(part, dict) and part.get("type") == "image_url":
                urls.append(part["image_url"]["url"])
    return urls


def _last_user_texts(messages: list[dict]) -> list[str]:
    return [
        p["text"]
        for p in messages[-1]["content"]
        if isinstance(p, dict) and p.get("type") == "text"
    ]


class TestImageBudgetFreeze(unittest.TestCase):
    def setUp(self):
        self._saved_budget = os.environ.get("GUIEXP_IMAGE_BUDGET_MB")

    def tearDown(self):
        if self._saved_budget is None:
            os.environ.pop("GUIEXP_IMAGE_BUDGET_MB", None)
        else:
            os.environ["GUIEXP_IMAGE_BUDGET_MB"] = self._saved_budget

    def test_under_budget_is_byte_identical_noop(self):
        """An episode whose images all fit must build requests exactly as before."""
        os.environ.pop("GUIEXP_IMAGE_BUDGET_MB", None)  # default 25MB budget
        guarded_client = _CapturingClient()
        agent = GuiAgent(model="m", client=guarded_client)
        img = _b64_image(600_000)  # ~0.8MB as a data URL; 3 images << 25MB default
        usages = []
        for i in range(3):
            reply, usage = agent.act("goal text" if i == 0 else None, _obs(img))
            usages.append(usage)

        # Same episode with a practically unlimited budget (the unguarded build).
        os.environ["GUIEXP_IMAGE_BUDGET_MB"] = "4096"
        unguarded_client = _CapturingClient()
        unguarded = GuiAgent(model="m", client=unguarded_client)
        for i in range(3):
            unguarded.act("goal text" if i == 0 else None, _obs(img))

        self.assertEqual(guarded_client.calls, unguarded_client.calls)  # deep equality
        for a, b in zip(guarded_client.calls, unguarded_client.calls):
            self.assertEqual(json.dumps(a), json.dumps(b))  # byte-identical serialization
        self.assertEqual(agent.history, unguarded.history)
        self.assertIsNone(agent.image_budget_event)  # guard never activated
        self.assertTrue(all("image_budget_freeze" not in u for u in usages))

    def test_crossing_step_freezes(self):
        """The first screenshot that would not fit is omitted; earlier images stay."""
        os.environ["GUIEXP_IMAGE_BUDGET_MB"] = "2"  # 2 MiB
        client = _CapturingClient()
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)  # data URL is 800_022 chars; 2 fit, 3 do not
        url_len = len(DATA_URL_PREFIX) + len(img)
        for _ in range(3):
            agent.act(None, _obs(img))

        self.assertEqual(len(_image_urls(client.calls[0])), 1)  # included
        self.assertEqual(len(_image_urls(client.calls[1])), 2)  # still fits
        # Third would cross the budget: omitted, frozen set unchanged.
        self.assertEqual(_image_urls(client.calls[2]), _image_urls(client.calls[1]))
        # The frozen turn keeps its observation text (URL line + interface tree).
        texts = _last_user_texts(client.calls[2])
        self.assertTrue(any("Current URL: http://localhost:5001/map" in t for t in texts))
        self.assertTrue(any("[b1] button 'Layers'" in t for t in texts))

        ev = agent.image_budget_event
        self.assertEqual(ev["images_kept"], 2)
        self.assertEqual(ev["bytes_kept"], 2 * url_len)
        self.assertEqual(ev["bytes_omitted"], url_len)
        self.assertEqual(ev["budget_bytes"], 2 * MB)
        self.assertEqual(ev["text_only_turns_after_freeze"], 0)

    def test_post_freeze_prefix_is_byte_identical(self):
        """Everything up to and including the freeze turn never changes again."""
        os.environ["GUIEXP_IMAGE_BUDGET_MB"] = "2"
        client = _CapturingClient()
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        agent.act(None, _obs(img))
        agent.act(None, _obs(img))
        agent.act(None, _obs(img))  # freeze happens here
        agent.act(None, _obs(img))  # a later step

        frozen_req, later_req = client.calls[2], client.calls[3]
        prefix = later_req[: len(frozen_req)]
        self.assertEqual(prefix, frozen_req)  # same turns, same content, deep-equal
        self.assertEqual(json.dumps(prefix), json.dumps(frozen_req))  # byte-identical
        # The already-included images are still there, untouched (no eviction).
        self.assertEqual(_image_urls(later_req), _image_urls(frozen_req))
        self.assertEqual(len(_image_urls(later_req)), 2)

    def test_no_images_after_freeze(self):
        """Post-freeze turns are text-only; the image set stays at the freeze size."""
        os.environ["GUIEXP_IMAGE_BUDGET_MB"] = "2"
        client = _CapturingClient()
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        for _ in range(5):  # freeze at call index 2, two text-only turns after
            agent.act(None, _obs(img))

        frozen_urls = _image_urls(client.calls[2])
        self.assertEqual(len(frozen_urls), 2)
        for req in client.calls[3:]:
            self.assertEqual(_image_urls(req), frozen_urls)  # no post-freeze image ever
            self.assertTrue(
                all(p.get("type") != "image_url" for p in req[-1]["content"])
            )  # the new turn itself carries no image
            self.assertLessEqual(
                sum(len(u) for u in _image_urls(req)), image_budget_bytes()
            )
        # History retains exactly the frozen images (nothing was rewritten).
        history_images = [
            p
            for msg in agent.history
            if msg["role"] == "user"
            for p in msg["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"
        ]
        self.assertEqual(len(history_images), 2)
        self.assertEqual(agent.image_budget_event["text_only_turns_after_freeze"], 2)

    def test_budget_env_override(self):
        """GUIEXP_IMAGE_BUDGET_MB lowers the budget and triggers earlier freezes."""
        os.environ["GUIEXP_IMAGE_BUDGET_MB"] = "1"
        self.assertEqual(image_budget_bytes(), MB)
        client = _CapturingClient()
        agent = GuiAgent(model="m", client=client)
        img = _b64_image(600_000)
        agent.act(None, _obs(img))  # 800_022 <= 1 MiB: still included
        agent.act(None, _obs(img))  # 1_600_044 > 1 MiB: freeze one step earlier
        self.assertEqual(len(_image_urls(client.calls[0])), 1)
        self.assertEqual(_image_urls(client.calls[1]), _image_urls(client.calls[0]))
        self.assertEqual(agent.image_budget_event["images_kept"], 1)

        # Tiny budget: the very first screenshot alone exceeds it -> text-only
        # episode, images_kept == 0, observation text still present.
        os.environ["GUIEXP_IMAGE_BUDGET_MB"] = "0.5"
        client2 = _CapturingClient()
        agent2 = GuiAgent(model="m", client=client2)
        agent2.act(None, _obs(_b64_image(900_000)))
        self.assertEqual(_image_urls(client2.calls[0]), [])
        self.assertEqual(agent2.image_budget_event["images_kept"], 0)
        self.assertGreater(agent2.image_budget_event["bytes_omitted"], 0)
        self.assertTrue(
            any("[b1] button 'Layers'" in t for t in _last_user_texts(client2.calls[0]))
        )


if __name__ == "__main__":
    unittest.main()

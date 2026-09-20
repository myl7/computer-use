"""Unit tests for the AndroidAgent reactive image-413 guard (v3 ladder).

Ported from web guiexp/tests/test_agent_image_413.py -- same semantics:
requests go out untouched (observations always attach full-quality PNG);
on HTTP 413 the pending request is reworked in place and retried
immediately, one tier per rejection:

    tier 1  compress_history  history images -> WebP q75 (same pixel size);
                              current image stays HD
    tier 2  compress_current  the current image too
    tier 3  strip_current     the current image goes (text-only step)
    tier 4  strip_history     most recent history image goes, repeating

Between passes the history is append-only and prefix-stable; already-webp
images are never double-compressed; the rejected 413 probe contributes no
usage record and the successful retry's usage is verbatim from its response.

Mock screenshots are small SYNTHETIC PNGs (noise, so WebP q75 is genuinely
smaller) built with Pillow in-test; no real PNGs, no emulator, no LLM.
Plain unittest (asserts), so it runs with the system python3; the package's
pytest setup runs it unchanged.
"""

from __future__ import annotations

import base64
import copy
import contextlib
import io
import json
import random
import unittest
from types import SimpleNamespace

from guiexp_android.agent import SYSTEM_PROMPT, AndroidAgent, _is_image_channel_error

try:
    from PIL import Image

    PIL_OK = True
except ImportError:  # encode tests are skipped; the no-op/500 tests still run
    PIL_OK = False

DATA_URL_PREFIX = "data:image/png;base64,"
WEBP_URL_PREFIX = "data:image/webp;base64,"
AX_PREFIX = (
    "Numbered list of UI elements on the current screen"
    " (the numbers are the element indexes the actions"
    " use):\n"
)
WIDTH, HEIGHT = 64, 48


class Fake413(Exception):
    """Canned OpenRouter image-budget rejection."""

    status_code = 413

    def __init__(self, message="Error: 413 - Downloaded image content cannot exceed 30MB"):
        super().__init__(message)


class FakeServerError(Exception):
    """A non-image channel error; must NOT trigger the guard."""

    status_code = 500


class _ScriptedClient:
    """Records a deep copy of every create() call; scripted failures by index."""

    def __init__(self, raise_413_at=(), other_errors=(), usage=None):
        self.calls = []  # deep copies, so in-place passes stay observable
        self.raise_413_at = set(raise_413_at)
        self.other_errors = dict(other_errors)  # call index -> exception
        self.usage = usage  # canned successful-response usage (None = default)
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, model=None, messages=None, **_kwargs):
        idx = len(self.calls)
        self.calls.append(copy.deepcopy(messages))
        if idx in self.raise_413_at:
            raise Fake413()
        if idx in self.other_errors:
            raise self.other_errors[idx]
        usage = self.usage or SimpleNamespace(prompt_tokens=10, completion_tokens=1)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='done()'))],
            usage=usage,
        )


def _png_b64(width: int = WIDTH, height: int = HEIGHT, seed: int = 0) -> str:
    """A synthetic noisy screenshot: WebP q75 compresses it well below PNG."""
    rnd = random.Random(seed)
    img = Image.frombytes("RGB", (width, height), rnd.randbytes(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _obs(image_b64: str | None) -> dict:
    obs = {
        "url": "com.osmand/.MapActivity",
        "ax_tree_text": "[3] android.widget.Button 'Layers'",
    }
    if image_b64 is not None:
        obs["screenshot_b64"] = image_b64
    return obs


def _user_content(img_b64: str, goal: str | None = None) -> list[dict]:
    """The observation content parts exactly as _observation_parts builds them."""
    text = (goal.rstrip("\n") + "\n\n") if goal else ""
    text += "Current app: com.osmand/.MapActivity\n"
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": DATA_URL_PREFIX + img_b64}},
        {"type": "text", "text": AX_PREFIX + "[3] android.widget.Button 'Layers'"},
    ]


def _image_parts(messages: list[dict]) -> list[dict]:
    parts = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        for part in msg["content"]:
            if isinstance(part, dict) and part.get("type") == "image_url":
                parts.append(part)
    return parts


def _image_urls(messages: list[dict]) -> list[str]:
    return [p["image_url"]["url"] for p in _image_parts(messages)]


class TestImage413Guard(unittest.TestCase):
    def test_normal_episode_sends_untouched_requests(self):
        """No 413: every request is the full legacy build, nothing recorded."""
        client = _ScriptedClient()
        agent = AndroidAgent(model="m", client=client)
        img = "QUJD" * 1000  # arbitrary bytes; nothing ever decodes them here
        usages = []
        for step in range(3):
            reply, usage = agent.act("goal text" if step == 0 else None, _obs(img))
            usages.append(usage)

        expected = []
        for step in range(3):
            req = [{"role": "system", "content": SYSTEM_PROMPT}]
            for s in range(step):  # completed pairs enter history after the call
                req.append({"role": "user", "content": _user_content(img, "goal text" if s == 0 else None)})
                req.append({"role": "assistant", "content": "done()"})
            req.append({"role": "user", "content": _user_content(img, "goal text" if step == 0 else None)})
            expected.append(req)

        self.assertEqual(client.calls, expected)  # deep equality
        for got, want in zip(client.calls, expected):
            self.assertEqual(json.dumps(got), json.dumps(want))  # byte-identical
        self.assertEqual(len(_image_urls(client.calls[-1])), 3)  # full PNG history
        self.assertEqual(agent.image_413_events, [])
        self.assertTrue(all("image_413_events" not in u for u in usages))

    @unittest.skipUnless(PIL_OK, "Pillow required for encode tests")
    def test_413_compresses_history_only_current_stays_hd(self):
        """Tier 1: history -> WebP q75 (same dimensions, smaller); the current
        screenshot keeps its exact HD PNG bytes; text is untouched."""
        client = _ScriptedClient(raise_413_at=[2])  # step 2, first attempt
        agent = AndroidAgent(model="m", client=client)
        img = _png_b64()
        for step in range(3):
            reply, usage = agent.act(None, _obs(img))
            if step == 2:
                freeze_usage = usage  # the call whose first attempt got the 413

        self.assertEqual(len(client.calls), 4)  # steps 0,1 + step2 twice
        attempt1, attempt2 = client.calls[2], client.calls[3]
        urls1, urls2 = _image_urls(attempt1), _image_urls(attempt2)
        self.assertEqual(len(urls1), 3)
        self.assertTrue(all(u.startswith(DATA_URL_PREFIX) for u in urls1))
        self.assertEqual(len(urls2), 3)  # nothing stripped
        self.assertTrue(all(u.startswith(WEBP_URL_PREFIX) for u in urls2[:2]))
        for old, new in zip(urls1[:2], urls2[:2]):
            img_obj = Image.open(io.BytesIO(base64.b64decode(new.split(",", 1)[1])))
            self.assertEqual(img_obj.size, (WIDTH, HEIGHT))  # dimensions kept
            self.assertLess(len(new), len(old))  # genuinely smaller
        # The current image is byte-identical HD PNG; text parts untouched.
        self.assertEqual(urls2[2], urls1[2])
        self.assertEqual(
            [p for p in attempt2[-1]["content"] if p.get("type") != "image_url"],
            [p for p in attempt1[-1]["content"] if p.get("type") != "image_url"],
        )
        self.assertTrue(any("[3] android.widget.Button 'Layers'" in p.get("text", "") for p in attempt2[-1]["content"]))

        event = agent.image_413_events[0]
        self.assertEqual(event["tier"], "compress_history")
        self.assertEqual(event["trigger"], "http_413")
        self.assertEqual(event["images"], 2)
        self.assertGreater(event["bytes_before"], event["bytes_after"])
        self.assertGreater(event["ratio"], 1)
        self.assertTrue("image_413_events" in freeze_usage)
        # Runner seam: the usage events are the agent's dicts, so the stamp lands.
        for ev in freeze_usage["image_413_events"]:
            ev["step"] = 2
        self.assertEqual(agent.image_413_events[0]["step"], 2)

    @unittest.skipUnless(PIL_OK, "Pillow required for encode tests")
    def test_second_413_runs_another_pass_without_double_compression(self):
        """A later 413 compresses only the newly-accumulated HD history images;
        the already-compressed ones keep their exact URLs."""
        client = _ScriptedClient(raise_413_at=[2, 5])
        agent = AndroidAgent(model="m", client=client)
        img = _png_b64()
        for step in range(5):
            agent.act(None, _obs(img))

        self.assertEqual(len(client.calls), 7)  # s0,s1 + s2 twice + s3 + s4 twice
        self.assertEqual([e["tier"] for e in agent.image_413_events],
                         ["compress_history", "compress_history"])
        urls_first = _image_urls(client.calls[3])
        urls_second = _image_urls(client.calls[6])
        # Pass 1 compressed u0, u1; pass 2 compressed only u2, u3 (the HD
        # images accumulated since); the pass-1 results are byte-identical.
        self.assertEqual(urls_second[:2], urls_first[:2])
        self.assertTrue(all(u.startswith(WEBP_URL_PREFIX) for u in urls_second[:4]))
        self.assertEqual(agent.image_413_events[1]["images"], 2)
        # The current image at the second pass was again left HD.
        self.assertTrue(_image_urls(client.calls[5])[-1].startswith(DATA_URL_PREFIX))
        self.assertTrue(urls_second[-1].startswith(DATA_URL_PREFIX))

    @unittest.skipUnless(PIL_OK, "Pillow required for encode tests")
    def test_prefix_is_byte_stable_between_passes(self):
        """Everything up to and including the post-compression turn never
        changes until the next pass mutates history."""
        client = _ScriptedClient(raise_413_at=[2])
        agent = AndroidAgent(model="m", client=client)
        img = _png_b64()
        for _ in range(4):
            agent.act(None, _obs(img))

        compressed_req, later_req = client.calls[3], client.calls[4]
        prefix = later_req[: len(compressed_req)]
        self.assertEqual(prefix, compressed_req)  # same turns, same content
        self.assertEqual(json.dumps(prefix), json.dumps(compressed_req))  # byte-identical

    @unittest.skipUnless(PIL_OK, "Pillow required for encode tests")
    def test_stubborn_413_climbs_to_the_strip_tiers(self):
        """Compressed history, then compressed current, still 413: the final
        resort strips the current image, then history -- loudly."""
        stderr = io.StringIO()
        client = _ScriptedClient(raise_413_at=[1, 2, 3, 4])  # step 1, four times
        agent = AndroidAgent(model="m", client=client)
        img = _png_b64()
        with contextlib.redirect_stderr(stderr):
            agent.act(None, _obs(img))  # step 0: fine
            reply, usage = agent.act(None, _obs(img))  # step 1: climbs all tiers

        self.assertEqual(len(client.calls), 6)
        self.assertEqual(
            [e["tier"] for e in agent.image_413_events],
            ["compress_history", "compress_current", "strip_current", "strip_history"],
        )
        self.assertTrue(all(e["degenerate"] for e in agent.image_413_events[2:]))
        self.assertTrue(all(e["trigger"] == "http_413" for e in agent.image_413_events))
        self.assertEqual(_image_urls(client.calls[5]), [])  # everything stripped
        self.assertIn("WARNING", stderr.getvalue())
        self.assertEqual(stderr.getvalue().count("WARNING"), 2)  # both strips loud
        # With no state, the next step attaches a fresh HD PNG as usual; only
        # a further 413 would trigger another pass.
        agent.act(None, _obs(img))
        self.assertEqual(len(_image_urls(client.calls[6])), 1)
        self.assertTrue(_image_urls(client.calls[6])[-1].startswith(DATA_URL_PREFIX))

    @unittest.skipUnless(PIL_OK, "Pillow required for encode tests")
    def test_413_retry_is_immediate_and_single(self):
        """Exactly one immediate rework-and-retry on 413: two calls for the
        affected step, then success (no backoff loop, no parse re-asks)."""
        client = _ScriptedClient(raise_413_at=[0])
        agent = AndroidAgent(model="m", client=client)
        reply, usage = agent.act(None, _obs(_png_b64()))
        self.assertEqual(reply, "done()")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(usage["image_413_events"]), 1)

    def test_non_image_errors_propagate_untouched(self):
        """A 500 is neither reworked nor retried here: it propagates as
        before, with a single call and no guard events. Also pins the
        rejection predicate directly (needs no Pillow)."""
        client = _ScriptedClient(other_errors={0: FakeServerError("Error: 500 - boom")})
        agent = AndroidAgent(model="m", client=client)
        with self.assertRaises(FakeServerError):
            agent.act(None, _obs(None))  # no screenshot: nothing to compress
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(agent.image_413_events, [])

        self.assertTrue(_is_image_channel_error(Fake413()))
        self.assertFalse(_is_image_channel_error(FakeServerError("Error: 500 - boom")))

        class _MessageOnly(Exception):
            pass

        self.assertTrue(
            _is_image_channel_error(_MessageOnly("Downloaded image content cannot exceed 30MB"))
        )


if __name__ == "__main__":
    unittest.main()

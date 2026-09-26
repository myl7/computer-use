"""Actual-payload pixel preservation and SDK wire-size tests, without network."""
import base64
import copy
import json
import struct
import zlib

import cv2
import httpx
import numpy as np
import pytest
from openai import OpenAI

from guiexp_android.lossless_transport_v6 import (
    LosslessTransport, PNG_PREFIX, TransportPayloadStop, wire_bytes,
)
from guiexp_android.budget_client_v6 import BudgetClientV6, BudgetLedgerV6, validate_metadata
from guiexp_android.tests.test_budget_matched import MODEL, SDK
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android.tests.test_budget_matched_v4 import metadata
from guiexp_android.tests.test_budget_matched import Response
from guiexp_android.tests.test_budget_matched_v3 import SequenceSDK, APITimeoutError


def png_url(pixels):
    ok, encoded = cv2.imencode(".png", pixels, [cv2.IMWRITE_PNG_COMPRESSION, 0])
    assert ok
    return PNG_PREFIX + base64.b64encode(encoded).decode("ascii")


def pixels(url):
    return cv2.imdecode(np.frombuffer(base64.b64decode(url.split(",", 1)[1]), np.uint8), cv2.IMREAD_UNCHANGED)


def payload(urls):
    return {"model": MODEL, "messages": [
        {"role": "system", "content": "Keep every observation."},
        {"role": "user", "content": [{"type": "text", "text": "Goal 你好"},
                                         {"type": "image_url", "image_url": {"url": urls[0], "detail": "high"}}]},
        {"role": "assistant", "content": "Action one"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}} for url in urls[1:]]},
    ], "temperature": 0, "max_tokens": 4096, "stream": False,
        "extra_body": {"provider": {"only": ["relace"], "allow_fallbacks": False}, "usage": {"include": True}}}


def image_urls(p):
    return [part["image_url"]["url"] for m in p["messages"] if isinstance(m.get("content"), list)
            for part in m["content"] if part.get("type") == "image_url"]


def test_preserves_actual_pixels_som_marks_order_duplicates_and_caller_history():
    red = np.zeros((64, 96, 3), np.uint8)
    red[:, :, 2] = 255
    # These marks stand in for the actual image's SOM overlays. The helper
    # must not replace this frame with an unmarked/raw disk screenshot.
    cv2.rectangle(red, (4, 5), (40, 30), (0, 255, 0), 2)
    blue = np.full((64, 96, 3), [255, 0, 0], np.uint8)
    urls = [png_url(red), png_url(blue), png_url(red)]
    before = payload(urls)
    saved = copy.deepcopy(before)
    after, report = LosslessTransport().prepare(before)
    assert before == saved
    assert len(after["messages"]) == len(before["messages"])
    assert report["image_count"] == 3
    assert report["total_known_image_pixels"] == 3 * 64 * 96
    assert after["messages"][1]["content"][0] == before["messages"][1]["content"][0]
    assert after["messages"][1]["content"][1]["image_url"]["detail"] == "high"
    for original, converted in zip(urls, image_urls(after)):
        assert np.array_equal(pixels(original), pixels(converted))
    assert image_urls(after)[0] == image_urls(after)[2]
    assert report["images"][2]["cache_hit"] is True
    assert report["serialized_body_bytes"] < report["original_serialized_body_bytes"]


@pytest.mark.parametrize("dtype,channels", [(np.uint8, 1), (np.uint8, 3), (np.uint8, 4), (np.uint16, 1)])
def test_dtype_shape_and_pixel_values_roundtrip(dtype, channels):
    shape = (31, 29) if channels == 1 else (31, 29, channels)
    original = (np.arange(np.prod(shape), dtype=np.uint32).reshape(shape) % 251).astype(dtype)
    converted, info = LosslessTransport().recode(png_url(original))
    decoded = pixels(converted)
    assert decoded.dtype == original.dtype and decoded.shape == original.shape
    assert np.array_equal(decoded, original)
    assert info["pixel_verified"] is True


def test_webp_mismatch_falls_back_to_lossless_png(monkeypatch):
    import guiexp_android.lossless_transport_v6 as module
    original_equal = module.roundtrip_equal
    monkeypatch.setattr(module, "roundtrip_equal", lambda p, b: False if b.startswith(b"RIFF") else original_equal(p, b))
    original = np.zeros((50, 50, 3), np.uint8)
    converted, info = LosslessTransport().recode(png_url(original))
    assert converted.startswith(PNG_PREFIX)
    assert info["encoding"] == "png_compression9"
    assert np.array_equal(pixels(converted), original)


def test_ancillary_png_metadata_retains_original_bytes():
    url = png_url(np.zeros((8, 8, 3), np.uint8))
    original = base64.b64decode(url[len(PNG_PREFIX):])
    # Add a valid metadata chunk after IHDR. Even metadata that appears benign
    # is conservatively retained rather than changing file interpretation.
    content = b"tEXt" + b"Comment\x00unchanged"
    chunk = struct.pack(">I", len(content)-4) + content + struct.pack(">I", zlib.crc32(content) & 0xffffffff)
    modified = original[:33] + chunk + original[33:]
    url = PNG_PREFIX + base64.b64encode(modified).decode("ascii")
    converted, info = LosslessTransport().recode(url)
    assert converted == url and info["encoding"] == "png_original"


def test_exact_wire_size_matches_real_sdk_serialization_without_network():
    source = png_url(np.zeros((8, 8, 3), np.uint8))
    prepared, info = LosslessTransport().prepare(payload([source, source]))
    seen = []
    def handler(request):
        seen.append(request.content)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
                                        "model": MODEL, "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0}})
    sdk = OpenAI(api_key="unit-test-not-a-real-key", base_url="https://openrouter.ai/api/v1", max_retries=0,
                 http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    sdk.chat.completions.create(**prepared)
    sdk.close()
    assert len(seen) == 1
    assert len(seen[0]) == info["serialized_body_bytes"] == wire_bytes(prepared)
    actual = json.loads(seen[0])
    assert actual["provider"] == prepared["extra_body"]["provider"]
    assert len(image_urls(actual)) == 2


def test_oversize_is_logged_and_stops_before_metadata_budget_or_sdk(tmp_path, monkeypatch):
    import guiexp_android.lossless_transport_v6 as transport_module
    import guiexp_android.budget_client_v6 as client_module
    monkeypatch.setattr(transport_module, "MAX_WIRE_BYTES", 1000)
    monkeypatch.setattr(client_module, "MAX_WIRE_BYTES", 1000)
    clock = Clock()
    ledger = BudgetLedgerV6(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SDK()
    gets = []
    def fetch(model):
        gets.append(model)
        return metadata(model)
    client = BudgetClientV6(ledger, {MODEL: validate_metadata(MODEL, metadata(MODEL))},
                            sdk, metadata_fetcher=fetch, sleep=clock.sleep)
    client.begin_episode("oversize")
    with pytest.raises(TransportPayloadStop, match="full history retained"):
        client.create(model=MODEL, messages=[{"role": "user", "content": "x" * 2000}], temperature=0)
    assert sdk.calls == [] and gets == [] and ledger.summary()["calls"] == 0
    with ledger.connect() as db:
        report = json.loads(db.execute("SELECT report_json FROM transport_metrics_v6").fetchone()[0])
    assert report["serialized_body_bytes"] > 1000 and report["within_local_limit"] is False


def test_corrupt_png_rejected_and_extra_body_cannot_override_budget_model():
    with pytest.raises(TransportPayloadStop):
        LosslessTransport().recode(PNG_PREFIX + "not valid base64")
    with pytest.raises(TransportPayloadStop):
        wire_bytes({"model": MODEL, "extra_body": {"model": "unbounded-model"}})


def test_physical_retry_reuses_identical_transformed_images_and_one_metric(tmp_path):
    clock = Clock()
    ledger = BudgetLedgerV6(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SequenceSDK([APITimeoutError(), Response("0.01")])
    gets = []
    def fetch(model):
        gets.append(model)
        return metadata(model)
    client = BudgetClientV6(ledger, {MODEL: validate_metadata(MODEL, metadata(MODEL))},
                            sdk, metadata_fetcher=fetch, sleep=clock.sleep)
    client.begin_episode("retry-with-images")
    url = png_url(np.zeros((32, 32, 3), np.uint8))
    p = payload([url, url])
    client.create(model=MODEL, messages=p["messages"], temperature=0)
    assert len(sdk.calls) == 2 and sdk.calls[0] == sdk.calls[1]
    assert image_urls(sdk.calls[0]) == image_urls(sdk.calls[1])
    assert len(gets) == 1 and clock.now() >= 1060
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM transport_metrics_v6").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 2
    assert ledger.summary()["actual_usd"] == "0.01"
    assert ledger.summary()["unresolved_reserved_usd"] == "0.09560064"

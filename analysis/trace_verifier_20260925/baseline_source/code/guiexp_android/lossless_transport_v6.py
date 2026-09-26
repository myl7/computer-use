"""Lossless transport-only image recoding and exact JSON-body measurement.

No image is resized, removed, deduplicated, reordered, or converted lossily.
The caller's message/history objects and saved screenshots remain unchanged.
32 MiB is a local guard derived from Relace's public request-body limit, not
an assurance that every intervening gateway accepts that size or image count.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import struct
import zlib
from collections import OrderedDict

import cv2
import numpy as np

from .budget_client import BudgetStop

MAX_WIRE_BYTES = 32 * 1024 * 1024
PNG_PREFIX = "data:image/png;base64,"
WEBP_PREFIX = "data:image/webp;base64,"


class TransportPayloadStop(BudgetStop):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def codec_versions():
    backend = next((line.strip().split(":", 1)[1].strip()
                    for line in cv2.getBuildInformation().splitlines()
                    if line.strip().startswith("WEBP:")), None)
    return {"opencv": cv2.__version__, "numpy": np.__version__,
            "webp_backend": backend, "webp_backend_reported": backend is not None}


def plain_png(data):
    """Only recode simple PNGs without colour/profile/orientation metadata."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise TransportPayloadStop("An image declared as PNG has an invalid signature.")
    at = 8
    chunks = []
    while at + 12 <= len(data):
        size = struct.unpack(">I", data[at:at+4])[0]
        tag = data[at+4:at+8]
        end = at + 12 + size
        if end > len(data):
            raise TransportPayloadStop("Truncated PNG; no request sent.")
        expected = struct.unpack(">I", data[end-4:end])[0]
        if zlib.crc32(data[at+4:end-4]) & 0xffffffff != expected:
            raise TransportPayloadStop("Invalid PNG checksum; no request sent.")
        chunks.append(tag)
        at = end
        if tag == b"IEND":
            break
    if at != len(data) or not chunks or chunks[0] != b"IHDR" or chunks[-1] != b"IEND":
        raise TransportPayloadStop("Invalid PNG chunk sequence; no request sent.")
    return all(tag in (b"IHDR", b"IDAT", b"IEND") for tag in chunks)


def roundtrip_equal(original, encoded):
    decoded = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_UNCHANGED)
    return (decoded is not None and decoded.dtype == original.dtype and
            decoded.shape == original.shape and np.array_equal(decoded, original))


class LosslessTransport:
    def __init__(self, cache_size=128):
        self.cache_size = cache_size
        self.cache = OrderedDict()

    def recode(self, url):
        if not isinstance(url, str):
            raise TransportPayloadStop("Image URL is not a string.")
        if not url.startswith(PNG_PREFIX):
            # Already-encoded other image types/URLs are never rewritten.
            return url, {"encoding": "unchanged_non_png", "pixel_verified": False}
        try:
            data = base64.b64decode(url[len(PNG_PREFIX):], validate=True)
        except (binascii.Error, ValueError):
            raise TransportPayloadStop("Malformed base64 PNG; no request sent.") from None
        key = sha(data)
        if key in self.cache:
            result, info = self.cache.pop(key)
            self.cache[key] = (result, info)
            return result, dict(info, cache_hit=True)
        simple = plain_png(data)
        pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if pixels is None:
            raise TransportPayloadStop("PNG could not be decoded; no request sent.")
        info = {"input_sha256": key, "pixel_sha256": sha(pixels.tobytes()),
                "shape": list(pixels.shape), "dtype": str(pixels.dtype),
                "input_bytes": len(data), "pixel_verified": True, "cache_hit": False}
        candidates = [(len(data), 2, "png_original", data, PNG_PREFIX)]
        if simple:
            # WebP >100 selects lossless mode in OpenCV. Its output must still
            # pass a full dtype/shape/pixel comparison, including alpha bytes.
            for codec, ext, params, prefix, priority in (
                ("webp_lossless", ".webp", [cv2.IMWRITE_WEBP_QUALITY, 101], WEBP_PREFIX, 0),
                ("png_compression9", ".png", [cv2.IMWRITE_PNG_COMPRESSION, 9], PNG_PREFIX, 1),
            ):
                # WebP cannot preserve 16-bit or single-channel arrays in
                # their original representation; PNG remains lossless there.
                if codec == "webp_lossless" and (pixels.dtype != np.uint8 or pixels.ndim != 3):
                    continue
                try:
                    ok, encoded = cv2.imencode(ext, pixels, params)
                    encoded = encoded.tobytes() if ok else b""
                except cv2.error:
                    encoded = b""
                if encoded and roundtrip_equal(pixels, encoded):
                    candidates.append((len(encoded), priority, codec, encoded, prefix))
        else:
            info["fallback_reason"] = "PNG has metadata chunks; keep original bytes to preserve interpretation"
        _, _, codec, encoded, prefix = min(
            candidates, key=lambda item: (len(item[4]) + 4 * ((item[0] + 2) // 3), item[1]))
        result = prefix + base64.b64encode(encoded).decode("ascii")
        info.update(encoding=codec, output_bytes=len(encoded), output_sha256=sha(encoded))
        self.cache[key] = (result, info)
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return result, info

    def prepare(self, payload):
        original_bytes = wire_bytes(payload)
        result = copy.deepcopy(payload)
        images = []
        for message_index, message in enumerate(result.get("messages", [])):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part_index, part in enumerate(content):
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                image_url = part.get("image_url")
                if not isinstance(image_url, dict) or "url" not in image_url:
                    raise TransportPayloadStop("Malformed image content block.")
                image_url["url"], info = self.recode(image_url["url"])
                images.append(dict(info, message_index=message_index, part_index=part_index))
        actual_bytes = wire_bytes(result)
        report = {"original_serialized_body_bytes": original_bytes,
                  "codec_runtime": codec_versions(),
                  "serialized_body_bytes": actual_bytes, "image_count": len(images),
                  "total_known_image_pixels": sum(item["shape"][0] * item["shape"][1] for item in images if "shape" in item),
                  "local_body_limit_bytes": MAX_WIRE_BYTES,
                  "within_local_limit": actual_bytes <= MAX_WIRE_BYTES,
                  "images": images,
                  "content_policy": "same pixels, dimensions, full messages, order, and duplicates",
                  "http_gzip": False,
                  "limit_scope": "Relace documentation: 32 MiB; other/intermediate constraints are unknown"}
        return result, report


def wire_body(payload):
    """Mirror OpenAI extra_body merging for these frozen request arguments."""
    body = {key: value for key, value in payload.items() if key != "extra_body"}
    extra = payload.get("extra_body") or {}
    if set(body) & set(extra):
        raise TransportPayloadStop("extra_body cannot override a bounded request parameter.")
    body.update(extra)
    return body


def wire_bytes(payload):
    # httpx's JSON request encoder uses these exact options. Tests compare
    # against a real SDK request captured by httpx.MockTransport, without I/O.
    return len(json.dumps(wire_body(payload), ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8"))

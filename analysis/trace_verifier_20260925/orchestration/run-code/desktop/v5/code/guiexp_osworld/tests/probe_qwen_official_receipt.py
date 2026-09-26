"""One authorized official-route text+image receipt probe; requires private env key."""

import json
import base64
import struct
import zlib

from guiexp_osworld.agent import OSWorldAgent
from guiexp_osworld.routing import qwen_official_client

def png_16x16() -> str:
    """Return a valid opaque 16x16 RGB PNG without external dependencies."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    width = height = 16
    # Each scanline starts with PNG filter 0, followed by 16 blue RGB pixels.
    raw = b"".join(b"\x00" + b"\x20\x70\xd0" * width for _ in range(height))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def main():
    client = qwen_official_client()
    response = client.chat.completions.create(
        model="qwen/qwen3.8-flash",
        temperature=0.0,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Reply with the single word OK."},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + png_16x16()}},
        ]}],
    )
    usage = OSWorldAgent._usage(response)
    print(json.dumps({"finish_reason": response.choices[0].finish_reason,
                      "route_receipt": usage["route_receipt"]}, indent=2))


if __name__ == "__main__":
    main()

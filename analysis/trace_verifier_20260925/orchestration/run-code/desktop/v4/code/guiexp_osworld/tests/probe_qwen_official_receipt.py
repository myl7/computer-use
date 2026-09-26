"""One authorized official-route text+image receipt probe; requires private env key."""

import json

from guiexp_osworld.agent import OSWorldAgent
from guiexp_osworld.routing import qwen_official_client

PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def main():
    client = qwen_official_client()
    response = client.chat.completions.create(
        model="qwen/qwen3.8-flash",
        temperature=0.0,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Reply with the single word OK."},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + PNG_1X1}},
        ]}],
    )
    usage = OSWorldAgent._usage(response)
    print(json.dumps({"finish_reason": response.choices[0].finish_reason,
                      "route_receipt": usage["route_receipt"]}, indent=2))


if __name__ == "__main__":
    main()

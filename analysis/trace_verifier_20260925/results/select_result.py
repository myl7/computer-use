"""Select immutable raw evidence for one canonical aggregation identity."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).resolve().parent / "evidence-selection.json"
PROTOCOL = "three-building-model-extracted-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True)
    parser.add_argument("--platform", required=True, choices=("android", "desktop", "web"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--role", required=True, choices=("initial", "repeat"))
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    path = (ROOT / args.path).resolve()
    if not path.is_file() or ROOT not in path.parents:
        raise SystemExit("Selected result must be an existing file under the repository")
    raw = json.loads(path.read_text())
    if raw.get("protocol_id") != PROTOCOL:
        raise SystemExit("Selected result has the wrong protocol")
    for field, expected in (("platform", args.platform), ("model", args.model), ("family", args.family)):
        if str(raw.get(field, "")).lower() != expected.lower():
            raise SystemExit(f"Selected result {field} mismatch")
    entry = {
        "canonical_identity": {"platform": args.platform, "model": args.model,
                               "family": args.family, "attempt_id": args.attempt_id,
                               "attempt_role": args.role},
        "raw_path": str(path.relative_to(ROOT)), "raw_sha256": sha(path),
        "raw_attempt_id": raw.get("attempt_id"), "raw_attempt_role": raw.get("attempt_role"),
        "selection_reason": args.reason,
    }
    manifest = json.loads(MANIFEST.read_text())
    identity = entry["canonical_identity"]
    matches = [item for item in manifest["selections"] if item["canonical_identity"] == identity]
    if matches:
        if matches[0] == entry:
            print("Selection already recorded")
            return
        raise SystemExit("Canonical identity already has a different selection")
    manifest["selections"].append(entry)
    manifest["selections"].sort(key=lambda item: tuple(item["canonical_identity"][key]
                                                        for key in ("platform", "model", "family", "attempt_role", "attempt_id")))
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(entry, sort_keys=True))


if __name__ == "__main__":
    main()

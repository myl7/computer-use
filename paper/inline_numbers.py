#!/usr/bin/env python3
"""Inline every \\n...{} number macro in body.tex with its value from numbers.tex.

Run this after gen_numbers.py regenerates numbers.tex so the literal numbers
in body.tex follow the regenerated values. Idempotent: a second run replaces
nothing (no macro calls remain) unless new ones were introduced.

Usage: python3 inline_numbers.py [--dry]
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent

DEF_RE = re.compile(r"\\(?:providecommand|newcommand|renewcommand)\{\\(n\w+)\}\{([^{}]*)\}")
CALL_RE = re.compile(r"\\(n\w+)(\{\})?")


def main() -> int:
    dry = "--dry" in sys.argv
    defs = {m.group(1): m.group(2) for m in DEF_RE.finditer((ROOT / "numbers.tex").read_text())}
    body = (ROOT / "body.tex").read_text()

    replaced = 0
    missing = set()

    def repl(m: re.Match) -> str:
        nonlocal replaced
        name = m.group(1)
        if name in defs:
            replaced += 1
            return defs[name]
        missing.add(name + (m.group(2) or ""))
        return m.group(0)

    new_body = CALL_RE.sub(repl, body)

    print(f"definitions in numbers.tex : {len(defs)}")
    print(f"macro calls replaced       : {replaced}")
    if missing:
        print("left as-is (no definition) :")
        for name in sorted(missing):
            print(f"  {name}")
    if not dry:
        (ROOT / "body.tex").write_text(new_body)
        print("body.tex written")
    else:
        print("dry run, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())

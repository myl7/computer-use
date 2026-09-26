"""LLM-free end-to-end self-test: golden programs judged by the checkers.

A hand-written program per family drives the guest through ProgramDevice (the
SAME device compiled programs get) for an injected binding; ProgramRunner
resets, runs, and judges with the family checker. This validates the whole
env side of the protocol -- reset determinism, app launch, element
addressing, typing semantics, the save dialog, the file fetch, the checker --
with zero model calls.
"""

import sys

sys.path.insert(0, ".")

from guiexp_osworld import families, guest_env  # noqa: E402
from guiexp_osworld.program_runtime import ProgramRunner, program_from_source  # noqa: E402

GOLDEN_CALC = '''
import time

def program(device, binding):
    # headers, then the 2x2 integer block under them; each cell is addressed
    # by its A1-style name, never by coordinates
    for cell, field in (("A1", "header_a"), ("B1", "header_b"),
                        ("A2", "val_a2"), ("B2", "val_b2"),
                        ("A3", "val_a3"), ("B3", "val_b3")):
        index = device.find(name=cell, role="table-cell")
        if index is None:
            raise ValueError(f"cell {cell} not found")
        device.input_text(str(binding[field]), index=index)
    device.hotkey("ctrl", "s")
    time.sleep(2.0)
    place = device.find(contains="Desktop", role="label")
    if place is None:
        raise ValueError("Desktop place not found in save dialog")
    device.click(index=place)
    time.sleep(1.0)
    field = device.find(role="text", contains="Untitled")
    if field is None:
        raise ValueError("save-dialog Name entry not found")
    device.input_text(binding["file_name"], index=field)
    time.sleep(0.6)
    device.press("enter")   # default response = Save
    time.sleep(2.5)
    return True
'''

GOLDEN_WRITER = '''
import time

def program(device, binding):
    doc = device.find(role="paragraph")
    if doc is None:
        doc = device.find(role="document-text")
    if doc is None:
        raise ValueError("no document paragraph to type into")
    device.type_at_caret(binding["title"])
    time.sleep(0.4)
    device.press("enter")   # end of line 1
    device.press("enter")   # the empty second line
    device.type_at_caret(binding["body"])
    time.sleep(0.4)
    device.hotkey("ctrl", "s")
    time.sleep(2.0)
    place = device.find(contains="Desktop", role="label")
    if place is None:
        raise ValueError("Desktop place not found in save dialog")
    device.click(index=place)
    time.sleep(1.0)
    field = device.find(role="text", contains="Untitled")
    if field is None:
        raise ValueError("save-dialog Name entry not found")
    device.input_text(binding["file_name"], index=field)
    time.sleep(0.6)
    device.press("enter")
    time.sleep(2.5)
    return True
'''


def main() -> int:
    env = guest_env.OSWorldEnv(start_if_needed=False)
    runner = ProgramRunner(env)
    failures = 0
    try:
        for family, source in (("CalcTableSave", GOLDEN_CALC),
                               ("WriterMemoSave", GOLDEN_WRITER)):
            for seed in (1, 2):  # two injected bindings per family
                binding = families.params_to_binding(
                    family, families.instance_params(family, seed))
                _module, program = program_from_source(source)
                outcome = runner.run(program, binding, family)
                tag = "OK" if outcome["passed"] else f"FAIL {outcome['error']}"
                print(f"{family} seed {seed}: {tag}", flush=True)
                failures += 0 if outcome["passed"] else 1
                if not outcome["passed"]:
                    break
    finally:
        env.close()
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

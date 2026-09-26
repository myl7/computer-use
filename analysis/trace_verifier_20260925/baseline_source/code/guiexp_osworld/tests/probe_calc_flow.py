"""LLM-free live UI probe: drive the Calc flow with ProgramDevice, no model.

Validates reset -> app up -> cell addressing -> typing -> save dialog ->
Desktop -> filename -> saved file readable by the checker. Prints the
element list at the breakpoints so the flow's element vocabulary is known.
"""

import sys
import time

sys.path.insert(0, ".")

from guiexp_osworld import families, guest_env  # noqa: E402
from guiexp_osworld.program_runtime import ProgramDevice  # noqa: E402

binding = families.params_to_binding(
    "CalcTableSave", families.instance_params("CalcTableSave", 3))
print("binding:", binding)

env = guest_env.OSWorldEnv(start_if_needed=False)
task = guest_env.get_task("CalcTableSave", "compile", 3)
obs = env.reset(task)
print("active window:", obs["url"], "| elements:", obs["ax_elements"])

device = ProgramDevice(env, settle_s=1.2)


def dump(needle: str | None = None, limit: int = 30):
    records = device.elements()
    shown = 0
    for record in records:
        blob = f"{record['role']} {record['name']} {record['text']}"
        if needle is None or needle.lower() in blob.lower():
            print("  ", record["index"], record["role"], "|", record["name"][:40],
                  "|", record["text"][:40], "|", "editable" if record["editable"] else "")
            shown += 1
            if shown >= limit:
                break
    print("   (total elements:", len(records), ")")


# A1 should be selected by default after launch; type the first header.
idx = device.find(name="A1", role="table-cell")
print("A1 index:", idx)
device.input_text(str(binding["header_a"]), index=idx)
dump(needle="A1", limit=6)
dump(needle="B1", limit=6)

print("-- press tab (commit A1, move right to B1)")
device.press("tab")
idx = device.find(name="B1", role="table-cell")
print("B1 index:", idx)
device.input_text(str(binding["header_b"]), index=idx)

print("-- enter (commit B1, move to B2)")
device.press("enter")
for cell, field in (("B2", "val_b2"), ("A2", "val_a2"), ("A3", "val_a3"), ("B3", "val_b3")):
    if cell != "B2":
        # navigate: from B2 pattern: type B2 then Enter twice gets to B3? We
        # will type each cell by clicking its address instead (robust).
        pass
    idx = device.find(name=cell, role="table-cell")
    print(cell, "index:", idx)
    device.input_text(str(binding[field]), index=idx)

time.sleep(1.0)
for cell in ("A1", "B1", "A2", "B2", "A3", "B3"):
    dump(needle=f'name="{cell}"', limit=2)

print("-- ctrl+s (save dialog)")
device.hotkey("ctrl", "s")
time.sleep(2.5)
print("active window:", device.active_window())
dump(needle="Save", limit=20)
dump(needle="Desktop", limit=8)
dump(needle="Name", limit=8)
dump(needle="text", limit=8)

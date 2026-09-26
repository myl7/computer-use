"""Continuation probe: finish the Calc save through the dialog, then judge."""

import sys
import time

sys.path.insert(0, ".")

from guiexp_osworld import families, guest_env  # noqa: E402
from guiexp_osworld.program_runtime import ProgramDevice  # noqa: E402

binding = families.params_to_binding(
    "CalcTableSave", families.instance_params("CalcTableSave", 3))
env = guest_env.OSWorldEnv(start_if_needed=False)
device = ProgramDevice(env, settle_s=1.2)

# The guest is where probe_calc_flow left it: save dialog open, cells typed.
print("active window:", device.active_window())

desktop = device.find(contains="Desktop", role="label")
print("Desktop place label:", desktop)
device.click(index=desktop)
time.sleep(1.0)

name_field = device.find(role="text", contains="Untitled")
print("Name entry:", name_field)
device.input_text(binding["file_name"], index=name_field)
time.sleep(0.8)

# Try Enter as the default-response activate.
device.press("enter")
time.sleep(2.5)
print("active window after enter:", device.active_window())

path = f"{families.GUEST_DESKTOP}/{binding['file_name']}"
print("file exists:", guest_env.guest_file_exists(path))
if guest_env.guest_file_exists(path):
    data = guest_env.get_guest_file(path)
    ok, err = families.check_calc(binding, data)
    print("CHECKER:", ok, err)
else:
    # dump the dialog state to see where we are
    for record in device.elements():
        if record["role"] in ("dialog", "push-button", "label", "text") and record["name"]:
            print("  ", record["index"], record["role"], "|", record["name"][:40],
                  "|", record["text"][:40])

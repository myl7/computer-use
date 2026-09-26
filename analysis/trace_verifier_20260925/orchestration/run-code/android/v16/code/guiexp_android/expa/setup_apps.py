"""One-time AVD app setup for the Exp A lane emulator.

Installs the benchmark apps the four Exp A families need — Google Contacts,
Markor, Simple Calendar Pro, OsmAnd — plus the AndroidWorld a11y forwarding
app the -grpc controller requires, and freezes the device clock to October
2023. This is what ANDROID_SETUP's one-time ``perform_emulator_setup`` did on
the Mac AVD; ``AndroidWorldEnv`` passes ``emulator_setup=False`` forever
after. Run ONCE per fresh AVD, with the lane emulator already booted:

    ../venv-expa/bin/python -m guiexp_android.expa.setup_apps \
        --console-port 5562 --grpc-port 8600 --avd guiexpExpA
"""

from __future__ import annotations

import argparse

from .lane import configure_lane, device_booted, load_env_file, wait_for_device


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--console-port", type=int, default=5562)
    parser.add_argument("--grpc-port", type=int, default=8600)
    parser.add_argument("--avd", default="guiexpExpA")
    parser.add_argument("--wait-s", type=float, default=600.0)
    args = parser.parse_args()

    load_env_file()
    configure_lane(args.console_port, args.grpc_port, args.avd)
    from .. import android_env

    if not device_booted() and not wait_for_device(args.wait_s):
        raise RuntimeError(f"emulator-{args.console_port} never finished booting")

    env = android_env.AndroidWorldEnv(
        console_port=args.console_port, grpc_port=args.grpc_port)
    try:
        from android_world.env.setup_device import apps, setup

        wanted = ("AndroidWorldApp", "ContactsApp", "MarkorApp",
                  "SimpleCalendarProApp", "OsmAndApp", "FilesApp")
        app_list = tuple(getattr(apps, name) for name in wanted)
        setup.setup_apps(env.aw_env, app_list=app_list)
        from android_world.utils import datetime_utils

        datetime_utils.setup_datetime(env.aw_env.controller)
        print(f"setup_apps done on emulator-{args.console_port}: "
              + ", ".join(a.app_name for a in app_list), flush=True)
    finally:
        env.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

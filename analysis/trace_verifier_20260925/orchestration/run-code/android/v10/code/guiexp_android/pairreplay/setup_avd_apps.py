"""One-time app setup for a paired-replay worker AVD (run once per AVD).

Installs exactly the apps the six replay families touch -- the preinstalled
Google Contacts and Files plus Markor, Simple Calendar Pro and OsmAnd (with
the Liechtenstein map) -- using android_world's own setup classes, so the
device state matches the AndroidWorldAvd the deploy records were measured
on. Also freezes the device clock the way every episode start does.

    .venv-android/bin/python -m guiexp_android.pairreplay.setup_avd_apps \
        --worker 0
"""

from __future__ import annotations

import argparse

from .. import android_env
from .worker import configure_worker_ports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker", type=int, required=True)
    args = parser.parse_args()

    configure_worker_ports(args.worker)
    if not android_env.emulator_is_running():
        android_env.boot_emulator(timeout=900.0)
        print(f"booted {android_env.AVD_NAME} ({android_env.SERIAL})", flush=True)

    from android_world.env import env_launcher, setup_device
    from android_world.env.setup_device import setup as aw_setup

    env = env_launcher.load_and_setup_env(
        console_port=android_env.CONSOLE_PORT,
        emulator_setup=False,
        freeze_datetime=True,
        adb_path=str(android_env.ADB_SHIM_PATH),  # same adb the episodes use
        grpc_port=android_env.GRPC_PORT,
    )
    apps = (
        setup_device.apps.ContactsApp,
        setup_device.apps.FilesApp,
        setup_device.apps.MarkorApp,
        setup_device.apps.SimpleCalendarProApp,
        setup_device.apps.OsmAndApp,
    )
    try:
        aw_setup.setup_apps(env, app_list=apps)
        print("app setup complete", flush=True)
    finally:
        try:
            env.close()
        except Exception:  # noqa: BLE001
            pass
    print("OK: AVD ready; you may shut it down and snapshot it", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

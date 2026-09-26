"""guiexp_osworld -- the OSWorld arm of the guiexp measurement protocol.

Ported from guiexp_android (the reference implementation, read-only) onto a
LibreOffice guest served by OSWorld's own desktop_env/server/main.py inside a
docker container (see docs/osworld-port-log-2026-09.md for the environment
decisions). Trajectory and build.json schemas are identical to the Android arm;
only the environment, action space and task families differ.
"""

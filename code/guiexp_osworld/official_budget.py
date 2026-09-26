"""Atomic shared CNY budget for official provider calls."""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from pathlib import Path


class OfficialBudgetExceeded(RuntimeError):
    pass


class OfficialBudgetLedger:
    def __init__(self, path: Path | str, cap_cny: float):
        self.path = Path(path).resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.cap_cny = float(cap_cny)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _mutate(self, change):
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if self.path.is_file():
                data = json.loads(self.path.read_text())
                if float(data["cap_cny"]) != self.cap_cny:
                    raise ValueError("official budget cap mismatch")
            else:
                data = {"schema_version": 1, "currency": "CNY",
                        "cap_cny": self.cap_cny, "reservations": {}}
            value = change(data)
            data["updated_at_unix"] = time.time()
            temporary = self.path.with_name(self.path.name + f".tmp-{os.getpid()}")
            temporary.write_text(json.dumps(data, indent=2) + "\n")
            os.replace(temporary, self.path)
            return value

    @staticmethod
    def _committed(data):
        return sum((entry["actual_cny"] if entry.get("actual_cny") is not None
                    else entry["reserved_cny"])
                   for entry in data["reservations"].values()
                   if entry["status"] in {"active", "settled", "uncertain"})

    def reserve(self, amount_cny: float, attempt_key: str) -> str:
        reservation_id = uuid.uuid4().hex

        def change(data):
            committed = self._committed(data)
            if committed + amount_cny > self.cap_cny + 1e-12:
                raise OfficialBudgetExceeded(
                    f"official CNY cap {self.cap_cny:g} would be exceeded")
            data["reservations"][reservation_id] = {
                "attempt_key": attempt_key, "reserved_cny": amount_cny,
                "actual_cny": None, "status": "active",
                "created_at_unix": time.time(),
            }
            return reservation_id

        return self._mutate(change)

    def settle(self, reservation_id: str, actual_cny: float):
        def change(data):
            entry = data["reservations"][reservation_id]
            if actual_cny > entry["reserved_cny"] + 1e-9:
                raise OfficialBudgetExceeded("actual call cost exceeded its hard reservation")
            entry.update(actual_cny=actual_cny, status="settled",
                         settled_at_unix=time.time())
        self._mutate(change)

    def mark_uncertain(self, reservation_id: str, error_type: str):
        def change(data):
            entry = data["reservations"][reservation_id]
            entry.update(status="uncertain", error_type=error_type,
                         settled_at_unix=time.time())
        self._mutate(change)

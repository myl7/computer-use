"""Offline tests for the read-only generation metadata reconciler."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from guiexp_android.reconcile_openrouter_generations import (
    _read_known_receipts,
    reconcile,
)


def _ledger(path: Path) -> None:
    database = sqlite3.connect(path)
    database.execute(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, episode TEXT, model TEXT, "
        "request_sha TEXT, reserved_nano INTEGER, actual_nano INTEGER, "
        "state TEXT, response_json TEXT, error_type TEXT, created REAL)"
    )
    database.execute(
        "CREATE TABLE error_metadata_v2 (call_id TEXT PRIMARY KEY, safe_json TEXT)"
    )
    database.execute(
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("safe-receipt", "ep", "model", "sha", 100, None, "uncertain",
         json.dumps({"id": "gen-safe-1"}), "BudgetStop", 1.0),
    )
    database.execute(
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("raw-receipt", "ep", "model", "sha", 100, None, "uncertain",
         json.dumps({"id": "gen-raw-1"}), "ProviderError", 2.0),
    )
    database.execute(
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("no-id", "ep", "model", "sha", 100, None, "uncertain",
         json.dumps({"choices": []}), "BudgetStop", 3.0),
    )
    database.execute(
        "INSERT INTO error_metadata_v2 VALUES (?,?)",
        ("safe-receipt", json.dumps({"generation_id": "gen-safe-1"})),
    )
    database.commit()
    database.close()


class _Response:
    def __init__(self, url, payload):
        self._url = url
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps(self._payload).encode("utf-8")

    def getcode(self):
        return 200

    def geturl(self):
        return self._url


class _Opener:
    def __init__(self):
        self.urls = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        return _Response(
            request.full_url,
            {"data": {"id": request.full_url.rsplit("=", 1)[-1], "total_cost": 0.00000005}},
        )


def test_read_known_receipts_uses_safe_id_table_and_counts_all_unknown(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    _ledger(path)
    total, receipts, grouped = _read_known_receipts(path)
    assert total == 3
    assert len(receipts) == 2
    assert len(grouped) == 2
    sources = {item["receipt_id"]: item["generation_id_source"] for item in receipts}
    assert sources == {"safe-receipt": "safe_generation_id", "raw-receipt": "raw_response_id"}


def test_reconcile_queries_only_known_ids_and_leaves_ledger_unchanged(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    _ledger(path)
    env = tmp_path / ".env"
    env.write_text(
        'OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"\n'
        'OPENROUTER_API_KEY="secret-value"\n',
        encoding="utf-8",
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    opener = _Opener()
    artifact, result = reconcile(
        path,
        env,
        tmp_path / "out",
        opener=opener,
        timestamp="20260915T000000Z",
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert result["input_unknown_unsettled_receipts"] == 3
    assert result["known_generation_ids"] == 2
    assert result["lookups_attempted"] == 2
    assert result["completion_requests"] == 0
    assert all("gen-safe-1" in url or "gen-raw-1" in url for url in opener.urls)
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert {item["generation_id_source"] for item in saved["known_receipt_provenance"]} == {
        "safe_generation_id", "raw_response_id"
    }
    assert "secret-value" not in artifact.read_text(encoding="utf-8")


"""Read-only lookup of known OpenRouter generation metadata.

This tool is intentionally separate from ``reconcile_saved_bills.py``.  It
never opens the ledger for writes and never changes receipt state.  It derives
generation IDs from preserved ``response_json`` fields in unsettled rows, then
issues one authenticated GET per distinct known ID at OpenRouter's official
``/generation?id=...`` endpoint.  It does not retry, follow redirects, or
send a completion request.

The output contains receipt provenance, safe HTTP metadata, and the provider's
JSON generation metadata.  The credential is used in memory only and is never
stored or printed.
"""

from __future__ import annotations

import argparse
import copy
import datetime as _datetime
import hashlib
import json
import re
import shlex
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping


OFFICIAL_BASE = "https://openrouter.ai/api/v1"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = DEFAULT_ROOT / "experimental-results/guiexp_android/revision_20260913/budget.sqlite3"
DEFAULT_OUT = DEFAULT_ROOT / "experimental-results/guiexp_android/selective_20260915/reconciliation"
DEFAULT_ENV = Path("/Users/myl/app/.env")
_GENERATION_ID = re.compile(r"^gen-[A-Za-z0-9][A-Za-z0-9._:-]{0,240}$")
_SECRET = re.compile(r"(?i)(?:bearer\s+\S+|sk-[A-Za-z0-9_-]{8,})")


class ReconciliationStop(RuntimeError):
    """A safe local stop before an unauthorized or unsafe request."""


def _safe_line(exc: BaseException) -> str:
    text = str(exc).splitlines()[0] if str(exc).splitlines() else ""
    return f"{type(exc).__name__}: {text[:240]}"


def _read_env(path: Path) -> str:
    """Read only the API key and verify the official origin."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReconciliationStop("credential file could not be read") from exc
    for line in lines:
        match = re.match(
            r"\s*(?:export\s+)?(OPENROUTER_API_KEY|OPENROUTER_BASE_URL)\s*=\s*(.*)$",
            line,
        )
        if not match:
            continue
        try:
            pieces = shlex.split(match.group(2), comments=True)
        except ValueError as exc:
            raise ReconciliationStop("credential file is malformed") from exc
        if len(pieces) != 1:
            raise ReconciliationStop("credential file is malformed")
        values[match.group(1)] = pieces[0]
    if not values.get("OPENROUTER_API_KEY"):
        raise ReconciliationStop("OPENROUTER_API_KEY is absent")
    if values.get("OPENROUTER_BASE_URL", "").rstrip("/") != OFFICIAL_BASE:
        raise ReconciliationStop("official OpenRouter origin is required")
    return values["OPENROUTER_API_KEY"]


def _read_known_receipts(
    ledger: Path,
) -> tuple[int, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Read unsettled rows and derive only IDs preserved in their JSON."""
    uri = f"file:{ledger.resolve()}?mode=ro"
    try:
        database = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise ReconciliationStop("shared ledger could not be opened read-only") from exc
    database.row_factory = sqlite3.Row
    try:
        database.execute("PRAGMA query_only=ON")
        rows = database.execute(
            "SELECT id,episode,model,reserved_nano,actual_nano,state,response_json,error_type,created "
            "FROM calls WHERE state='uncertain' AND actual_nano IS NULL ORDER BY created,id"
        ).fetchall()
        safe_ids: dict[str, str] = {}
        try:
            safe_rows = database.execute(
                "SELECT call_id,safe_json FROM error_metadata_v2"
            ).fetchall()
        except sqlite3.Error:
            safe_rows = []
        for safe_row in safe_rows:
            try:
                safe_json = json.loads(safe_row["safe_json"])
            except (TypeError, ValueError):
                continue
            safe_id = safe_json.get("generation_id") if isinstance(safe_json, dict) else None
            if isinstance(safe_id, str) and _GENERATION_ID.fullmatch(safe_id):
                safe_ids[safe_row["call_id"]] = safe_id
    except sqlite3.Error as exc:
        raise ReconciliationStop("shared ledger query failed") from exc
    finally:
        database.close()

    receipts: list[dict[str, Any]] = []
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        raw_text = row["response_json"]
        response: Any = {}
        try:
            response = json.loads(raw_text) if raw_text else {}
        except (TypeError, ValueError):
            response = {}
        raw_response_id = response.get("id") if isinstance(response, dict) else None
        if not isinstance(raw_response_id, str) or not _GENERATION_ID.fullmatch(raw_response_id):
            raw_response_id = None
        generation_id = safe_ids.get(row["id"])
        id_source = "safe_generation_id" if generation_id else None
        if generation_id is None and raw_response_id is not None:
            generation_id = raw_response_id
            id_source = "raw_response_id"
        if generation_id is None:
            usage = response.get("usage") if isinstance(response, dict) else None
            generation_id = usage.get("generation_id") if isinstance(usage, dict) else None
            if isinstance(generation_id, str) and _GENERATION_ID.fullmatch(generation_id):
                id_source = "raw_response_id"
            else:
                generation_id = None
        if generation_id is None:
            continue
        provenance = {
            "receipt_id": row["id"],
            "episode": row["episode"],
            "model": row["model"],
            "state": row["state"],
            "reserved_nano": row["reserved_nano"],
            "actual_nano": row["actual_nano"],
            "error_type": row["error_type"],
            "created": row["created"],
            "generation_id": generation_id,
            "generation_id_source": id_source,
            "raw_response_id": raw_response_id,
            "response_sha256": hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest(),
            "source": "shared-ledger-read-only",
        }
        receipts.append(provenance)
        by_id.setdefault(generation_id, []).append(provenance)
    return len(rows), receipts, by_id


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ReconciliationStop("redirect rejected")


def _redact(value: Any, credential: str) -> Any:
    """Keep JSON shape while preventing accidental credential persistence."""
    if isinstance(value, dict):
        return {str(key): _redact(item, credential) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, credential) for item in value]
    if isinstance(value, str):
        text = value.replace(credential, "[REDACTED]") if credential else value
        return _SECRET.sub("[REDACTED]", text)
    return value


def _cost(metadata: Any) -> tuple[str | None, str | None]:
    """Extract a finite nonnegative provider cost without inferring it."""
    candidates: list[tuple[str, Any]] = []
    if isinstance(metadata, dict):
        data = metadata.get("data")
        if isinstance(data, dict):
            for key in ("usage", "cost", "total_cost"):
                if key in data:
                    value = data[key]
                    if isinstance(value, dict) and "cost" in value:
                        candidates.append((f"data.{key}.cost", value["cost"]))
                    else:
                        candidates.append((f"data.{key}", value))
        for key in ("cost", "total_cost"):
            if key in metadata:
                candidates.append((key, metadata[key]))
    for field, value in candidates:
        if isinstance(value, bool) or value is None:
            continue
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if decimal.is_finite() and decimal >= 0:
            return str(decimal), field
    return None, None


def _within_reservations(cost: str | None, provenance: list[dict[str, Any]]) -> bool | None:
    if cost is None:
        return None
    try:
        cost_nano = int(
            (Decimal(cost) * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_CEILING)
        )
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None
    bounds = [item.get("reserved_nano") for item in provenance]
    if not bounds or any(type(value) is not int or value < 0 for value in bounds):
        return None
    return cost_nano <= min(bounds)


def _lookup(
    generation_id: str,
    credential: str,
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Perform one exact official GET for a ledger-derived ID."""
    if not _GENERATION_ID.fullmatch(generation_id):
        raise ReconciliationStop("lookup ID was not derived from a known generation ID")
    query = urllib.parse.urlencode({"id": generation_id})
    url = f"{OFFICIAL_BASE}/generation?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {credential}"},
        method="GET",
    )
    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=30)
        with response:
            body = response.read(2 * 1024 * 1024)
            status = int(response.getcode())
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        return {
            "generation_id": generation_id,
            "url": url,
            "lookup_status": "http_error",
            "http_status": int(exc.code),
            "error_type": "HTTPError",
        }
    except ReconciliationStop:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "generation_id": generation_id,
            "url": url,
            "lookup_status": "transport_error",
            "http_status": None,
            "error_type": type(exc).__name__,
        }
    if final_url != url:
        return {
            "generation_id": generation_id,
            "url": url,
            "lookup_status": "redirect_rejected",
            "http_status": status,
            "error_type": "UnexpectedFinalURL",
        }
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {
            "generation_id": generation_id,
            "url": url,
            "lookup_status": "invalid_json",
            "http_status": status,
            "error_type": "InvalidJSON",
        }
    if not isinstance(payload, dict):
        return {
            "generation_id": generation_id,
            "url": url,
            "lookup_status": "invalid_payload",
            "http_status": status,
            "error_type": "NonObjectJSON",
        }
    provider_id = payload.get("data", {}).get("id") if isinstance(payload.get("data"), dict) else None
    cost, cost_field = _cost(payload)
    return {
        "generation_id": generation_id,
        "url": url,
        "lookup_status": "returned" if status == 200 else "http_error",
        "http_status": status,
        "provider_id_matches": provider_id == generation_id,
        "authoritative_cost_usd": cost if status == 200 and provider_id == generation_id else None,
        "authoritative_cost_field": cost_field if status == 200 and provider_id == generation_id else None,
        "provider_metadata": payload if status == 200 else None,
    }


def reconcile(
    ledger: Path | str = DEFAULT_LEDGER,
    env_file: Path | str = DEFAULT_ENV,
    out_dir: Path | str = DEFAULT_OUT,
    *,
    opener: Callable[..., Any] | None = None,
    timestamp: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Run the bounded read-only reconciliation and write one new artifact."""
    ledger_path = Path(ledger).resolve()
    unknown_count, receipts, grouped = _read_known_receipts(ledger_path)
    credential = _read_env(Path(env_file))
    lookups: list[dict[str, Any]] = []
    for generation_id, provenance in grouped.items():
        item = _lookup(generation_id, credential, opener=opener)
        item["receipt_provenance"] = copy.deepcopy(provenance)
        item["authoritative_receipt_ids"] = [row["receipt_id"] for row in provenance] if item.get("authoritative_cost_usd") is not None else []
        item["within_reserved_bound"] = _within_reservations(item.get("authoritative_cost_usd"), provenance)
        item["safe_reconcile_feasible"] = bool(
            item.get("authoritative_cost_usd") is not None
            and item.get("provider_id_matches") is True
        )
        item = _redact(item, credential)
        lookups.append(item)
    stamp = timestamp or _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"\d{8}T\d{6}Z", stamp):
        raise ReconciliationStop("timestamp must be UTC YYYYMMDDTHHMMSSZ")
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / f"generation-reconciliation-{stamp}.json"
    summary = {
        "record_type": "openrouter-generation-reconciliation",
        "created_utc": stamp,
        "ledger": str(ledger_path),
        "ledger_mode": "read-only",
        "endpoint": f"{OFFICIAL_BASE}/generation?id=<ledger-derived-id>",
        "request_method": "GET",
        "redirects": "rejected",
        "completion_requests": 0,
        "input_unknown_unsettled_receipts": unknown_count,
        "receipts_with_generation_id": len(receipts),
        "known_generation_ids": len(grouped),
        "lookups_attempted": len(lookups),
        "authoritative_cost_count": sum(item.get("authoritative_cost_usd") is not None for item in lookups),
        "safe_reconcile_feasible_count": sum(item.get("safe_reconcile_feasible") is True for item in lookups),
        "authoritative_cost_receipt_count": sum(len(item.get("authoritative_receipt_ids") or []) for item in lookups),
        "known_receipt_count": len(receipts),
        "known_receipt_provenance": receipts,
        "lookups": lookups,
    }
    artifact.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        artifact, result = reconcile(args.ledger, args.env_file, args.out_dir)
    except ReconciliationStop as exc:
        print(f"reconciliation stopped: {exc}")
        return 2
    print(json.dumps({
        "artifact": str(artifact),
        "unknown_unsettled_receipts": result["input_unknown_unsettled_receipts"],
        "known_generation_ids": result["known_generation_ids"],
        "lookups_attempted": result["lookups_attempted"],
        "authoritative_cost_count": result["authoritative_cost_count"],
        "safe_reconcile_feasible_count": result["safe_reconcile_feasible_count"],
        "authoritative_cost_receipt_count": result["authoritative_cost_receipt_count"],
        "completion_requests": result["completion_requests"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

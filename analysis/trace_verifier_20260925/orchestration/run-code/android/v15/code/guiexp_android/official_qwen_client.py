"""Scoped OpenAI-compatible transport for official Qwen PAYG repeats."""

from __future__ import annotations

from pathlib import Path
from decimal import Decimal, InvalidOperation
import json
import fcntl

OFFICIAL_QWEN_BASE_URL = "https://maas.qianwenaiapi.com/compatible-mode/v1"
EXPERIMENT_MODEL = "qwen/qwen3.8-flash"
SERVED_MODEL = "qwen3.8-flash"
KEY_NAME = "QWEN_OFFICIAL_API_KEY"
BASE_NAME = "QWEN_OFFICIAL_BASE_URL"
MODEL_NAME = "QWEN_OFFICIAL_WIRE_MODEL"
BUDGET_NAME = "QWEN_OFFICIAL_ACCOUNT_BUDGET_CNY"
INPUT_CNY_PER_TOKEN = Decimal("0.0000008")
OUTPUT_CNY_PER_TOKEN = Decimal("0.0000027")
MAX_CALL_RESERVE_CNY = Decimal("5")
MAX_UNRESOLVED_CALLS = 2
OFFICIAL_HTTP_TIMEOUT_S = 600.0


def load_official_qwen_config(path: Path | str) -> dict:
    """Read the exact private route contract; never return key provenance."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, candidate = line.partition("=")
        key = key.strip()
        if key in (KEY_NAME, BASE_NAME, MODEL_NAME, BUDGET_NAME):
            values[key] = candidate.strip().strip('"').strip("'")
    missing = [name for name in (KEY_NAME, BASE_NAME, MODEL_NAME, BUDGET_NAME)
               if not values.get(name)]
    if missing:
        raise RuntimeError("private official Qwen environment is missing required names")
    if values[BASE_NAME].rstrip("/") != OFFICIAL_QWEN_BASE_URL.rstrip("/"):
        raise RuntimeError("official Qwen base URL does not match the frozen route")
    if values[MODEL_NAME] != SERVED_MODEL:
        raise RuntimeError("official Qwen wire model does not match the frozen served model")
    try:
        budget = Decimal(values[BUDGET_NAME])
    except InvalidOperation as exc:
        raise RuntimeError("official Qwen account budget is not numeric") from exc
    if budget <= 0:
        raise RuntimeError("official Qwen account budget must be positive")
    return {"api_key": values[KEY_NAME], "base_url": values[BASE_NAME],
            "wire_model": values[MODEL_NAME], "account_budget_cny": str(budget)}


class _MappedCompletions:
    def __init__(self, underlying, owner) -> None:
        self.underlying = underlying
        self.owner = owner

    def create(self, **kwargs):
        self.owner._before_call()
        requested = kwargs.get("model")
        if requested != EXPERIMENT_MODEL:
            raise ValueError(f"official Qwen route only accepts experiment model {EXPERIMENT_MODEL}")
        mapped = dict(kwargs)
        mapped["model"] = SERVED_MODEL
        try:
            response = self.underlying.create(**mapped)
        except Exception:
            # Retain the reservation: transport/provider failure billing is
            # unresolved, and releasing it could overspend the shared account.
            self.owner._mark_unresolved_call()
            raise
        self.owner._after_call(response)
        return response


class OfficialQwenClient:
    """Map the frozen experiment model ID to the official served model ID."""

    _route_provenance_template = {
        "route": "qwen_official_payg",
        "base_url": OFFICIAL_QWEN_BASE_URL,
        "experiment_model": EXPERIMENT_MODEL,
        "served_model": SERVED_MODEL,
        "temperature": 0.0,
        "generation_kwargs": "provider_defaults_except_temperature",
        "max_tokens": "provider_default",
        "billing_currency": "CNY",
    }

    def __init__(self, key_file: Path | str | None = None, underlying=None,
                 run_budget_cny: str | Decimal | None = None,
                 budget_ledger_path: Path | str | None = None) -> None:
        config = None
        if underlying is None:
            from openai import OpenAI

            if key_file is None:
                raise ValueError("official Qwen route requires a private key file")
            config = load_official_qwen_config(key_file)
            underlying_client = OpenAI(
                base_url=config["base_url"],
                api_key=config["api_key"],
                timeout=OFFICIAL_HTTP_TIMEOUT_S,
                max_retries=0,
            )
            underlying = underlying_client.chat.completions
        self.route_provenance = dict(self._route_provenance_template)
        if config is not None:
            self.route_provenance["account_budget_cny"] = config["account_budget_cny"]
        self.run_budget_cny = Decimal(str(run_budget_cny)) if run_budget_cny is not None else None
        if self.run_budget_cny is not None and self.run_budget_cny <= 0:
            raise ValueError("official Qwen run budget must be positive")
        if config is not None and self.run_budget_cny is not None:
            if self.run_budget_cny > Decimal(config["account_budget_cny"]):
                raise ValueError("official Qwen run budget exceeds account budget")
        self.budget_ledger_path = Path(budget_ledger_path) if budget_ledger_path else None
        if self.run_budget_cny is not None:
            if self.budget_ledger_path is None:
                raise ValueError("official Qwen budget requires a ledger path")
            self.route_provenance["run_budget_cny"] = str(self.run_budget_cny)
            self.route_provenance["budget_ledger_path"] = str(self.budget_ledger_path)
            self.route_provenance["max_call_reserve_cny"] = str(MAX_CALL_RESERVE_CNY)
            self.route_provenance["max_unresolved_calls"] = MAX_UNRESOLVED_CALLS
            self.route_provenance["http_timeout_s"] = OFFICIAL_HTTP_TIMEOUT_S
            self.route_provenance["budget_scope"] = "shared_android_all_qwen_repeats"
        self.chat = type("Chat", (), {"completions": _MappedCompletions(underlying, self)})()

    def _read_budget(self) -> dict:
        if self.budget_ledger_path is None or not self.budget_ledger_path.exists():
            return {"spent_cny": "0", "calls": 0, "blocked": False}
        return json.loads(self.budget_ledger_path.read_text())

    def _write_budget(self, record: dict) -> None:
        if self.budget_ledger_path is None:
            return
        self.budget_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.budget_ledger_path.with_suffix(self.budget_ledger_path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, indent=1))
        tmp.replace(self.budget_ledger_path)

    def _update_budget(self, update):
        lock_path = self.budget_ledger_path.with_suffix(
            self.budget_ledger_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            record = self._read_budget()
            result = update(record)
            self._write_budget(record)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return result

    def _before_call(self) -> None:
        if self.run_budget_cny is None:
            return
        def reserve(record):
            spent = Decimal(record.get("spent_cny", "0"))
            reserved = Decimal(record.get("reserved_cny", "0"))
            if record.get("blocked") or spent + reserved + MAX_CALL_RESERVE_CNY > self.run_budget_cny:
                raise RuntimeError("official Qwen shared Android budget is exhausted or unreconciled")
            if int(record.get("unresolved_calls", 0)) >= MAX_UNRESOLVED_CALLS:
                raise RuntimeError("official Qwen unresolved-call limit is exhausted")
            record["reserved_cny"] = str(reserved + MAX_CALL_RESERVE_CNY)
            record["calls_started"] = int(record.get("calls_started", 0)) + 1
            record["cap_cny"] = str(self.run_budget_cny)
        self._update_budget(reserve)

    def _mark_unresolved_call(self) -> None:
        if self.run_budget_cny is None:
            return
        def mark(record):
            record["unresolved_calls"] = int(record.get("unresolved_calls", 0)) + 1
            record["blocked"] = True
            record["block_reason"] = "call_failed_with_billing_unresolved"
        self._update_budget(mark)

    def _after_call(self, response) -> None:
        if self.run_budget_cny is None:
            return
        usage = official_usage_record(response)
        cost = Decimal(str(usage["cost_cny"]))
        def settle(record):
            reserved = Decimal(record.get("reserved_cny", "0"))
            if reserved < MAX_CALL_RESERVE_CNY:
                raise RuntimeError("official Qwen shared budget reservation is missing")
            spent = Decimal(record.get("spent_cny", "0")) + cost
            record["reserved_cny"] = str(reserved - MAX_CALL_RESERVE_CNY)
            record["spent_cny"] = str(spent)
            record["calls_completed"] = int(record.get("calls_completed", 0)) + 1
            record["blocked"] = (
                spent + Decimal(record["reserved_cny"]) + MAX_CALL_RESERVE_CNY
                > self.run_budget_cny)
            if spent > self.run_budget_cny:
                record["block_reason"] = "provider_charge_crossed_shared_budget"
        self._update_budget(settle)


def recover_shared_budget_ledger(path: Path | str, cap_cny: str | Decimal,
                                 operator_confirmed: bool) -> dict:
    """Unblock one bounded retry while retaining all unresolved exposure."""
    if not operator_confirmed:
        raise ValueError("explicit unresolved-billing confirmation is required")
    path = Path(path)
    cap = Decimal(str(cap_cny))
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        record = json.loads(path.read_text())
        if Decimal(str(record.get("cap_cny"))) != cap:
            raise ValueError("shared ledger cap does not match requested cap")
        unresolved = int(record.get("unresolved_calls", 0))
        reserved = Decimal(record.get("reserved_cny", "0"))
        spent = Decimal(record.get("spent_cny", "0"))
        if record.get("block_reason") != "call_failed_with_billing_unresolved":
            raise ValueError("ledger is not blocked on unresolved call billing")
        if unresolved <= 0 or unresolved >= MAX_UNRESOLVED_CALLS:
            raise ValueError("unresolved-call recovery budget is exhausted")
        if reserved < Decimal(unresolved) * MAX_CALL_RESERVE_CNY:
            raise ValueError("ledger does not retain the required unresolved reservation")
        if spent + reserved + MAX_CALL_RESERVE_CNY > cap:
            raise ValueError("new reservation would exceed the shared Android cap")
        record["blocked"] = False
        record["block_reason"] = None
        record.setdefault("operator_recoveries", []).append({
            "kind": "continue_with_unresolved_reservation_retained",
            "unresolved_calls": unresolved,
            "retained_reserved_cny": str(reserved),
            "next_call_reserve_cny": str(MAX_CALL_RESERVE_CNY),
        })
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, indent=1))
        tmp.replace(path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return record


def official_usage_record(response) -> dict:
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    cny = getattr(usage, "cost_cny", None)
    if cny is None and getattr(usage, "model_extra", None):
        cny = usage.model_extra.get("cost_cny")
    if cny is None:
        cny = getattr(usage, "cost", None)
    if cny is None and getattr(usage, "model_extra", None):
        cny = usage.model_extra.get("cost")
    if cny is None:
        cny = (Decimal(prompt) * INPUT_CNY_PER_TOKEN
               + Decimal(completion) * OUTPUT_CNY_PER_TOKEN)
        cost_source = "frozen_official_receipt_rates_20260926"
    else:
        cost_source = "provider_usage"
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_cny": str(cny),
        "cost_cny_source": cost_source,
        "response_model": getattr(response, "model", None),
    }

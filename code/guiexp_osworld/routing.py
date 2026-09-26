"""Scoped provider routing while preserving canonical experiment identities."""

from __future__ import annotations

import os
import json
from pathlib import Path

from .official_budget import OfficialBudgetLedger
from types import SimpleNamespace

QWEN_CANONICAL_MODEL = "qwen/qwen3.8-flash"
QWEN_OFFICIAL_MODEL = "qwen3.8-flash"
QWEN_OFFICIAL_BASE_URL = "https://maas.qianwenaiapi.com/compatible-mode/v1"
QWEN_PRICING_SOURCE = "https://help.aliyun.com/en/model-studio/qwen3-8-flash"


class _RoutedCompletions:
    def __init__(self, completions, route: dict):
        self._completions = completions
        self._route = route

    def create(self, **kwargs):
        canonical = kwargs.get("model")
        if canonical != self._route["canonical_model"]:
            raise ValueError(f"official route only supports {self._route['canonical_model']}")
        routed = dict(kwargs)
        routed["model"] = self._route["served_model"]
        ledger = OfficialBudgetLedger(
            os.environ["QWEN_OFFICIAL_BUDGET_LEDGER"],
            float(os.environ.get("QWEN_OFFICIAL_BUDGET_CAP_CNY", "20")))
        reservation_cny = float(os.environ.get(
            "QWEN_OFFICIAL_MAX_CALL_RESERVATION_CNY", "1.2"))
        reservation_id = ledger.reserve(
            reservation_cny, os.environ.get("GUIEXP_ATTEMPT_KEY", "unknown"))
        try:
            response = self._completions.create(**routed)
        except BaseException as exc:
            ledger.mark_uncertain(reservation_id, type(exc).__name__)
            raise
        usage = getattr(response, "usage", None)
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        rates = self._route["rates_cny_per_million"]
        cost_cny = ((prompt - cached) * rates["input"]
                    + cached * rates["cache_read"]
                    + completion * rates["output"]) / 1_000_000
        ledger.settle(reservation_id, cost_cny)
        receipt = {
            **self._route,
            "response_id": getattr(response, "id", None),
            "response_model": getattr(response, "model", None),
            "requested_temperature": kwargs.get("temperature"),
            "explicit_request_kwargs": sorted(k for k in kwargs if k != "messages"),
            "provider_defaults_left_unset": [
                key for key in ("max_tokens", "max_completion_tokens", "top_p", "seed")
                if key not in kwargs
            ],
            "usage": {"prompt_tokens": prompt, "cached_tokens": cached,
                      "completion_tokens": completion},
            "cost_cny": round(cost_cny, 10),
            "budget": {"cap_cny": ledger.cap_cny,
                       "reservation_cny": reservation_cny,
                       "reservation_id": reservation_id,
                       "ledger": str(ledger.path)},
        }
        object.__setattr__(response, "_guiexp_route_receipt", receipt)
        return response


class RoutedOpenAIClient:
    def __init__(self, client, route: dict):
        self._client = client
        self.route = route
        self.chat = SimpleNamespace(
            completions=_RoutedCompletions(client.chat.completions, route))


def qwen_official_client():
    """Build the official PAYG client. Secrets are accepted only via env."""
    from openai import OpenAI

    key = os.environ.get("QWEN_OFFICIAL_API_KEY")
    if not key:
        raise RuntimeError("QWEN_OFFICIAL_API_KEY is not set")
    base_url = os.environ.get("QWEN_OFFICIAL_BASE_URL", QWEN_OFFICIAL_BASE_URL)
    rates = {
        "input": float(os.environ.get("QWEN_OFFICIAL_INPUT_CNY_PER_M", "0.8")),
        "output": float(os.environ.get("QWEN_OFFICIAL_OUTPUT_CNY_PER_M", "2.7")),
        "cache_read": float(os.environ.get("QWEN_OFFICIAL_CACHE_CNY_PER_M", "0.1")),
    }
    route = {
        "route_id": "qwen-official-payg-v1",
        "provider": "Alibaba Model Studio official PAYG",
        "base_url": base_url,
        "canonical_model": QWEN_CANONICAL_MODEL,
        "served_model": QWEN_OFFICIAL_MODEL,
        "rates_cny_per_million": rates,
        "pricing_source": QWEN_PRICING_SOURCE,
        "pricing_note": "Configured nominal rates; console discounts are not inferred",
    }
    client = OpenAI(base_url=base_url, api_key=key, timeout=180.0, max_retries=2)
    return RoutedOpenAIClient(client, route)


def apply_route_config(path: Path | str, canonical_model: str) -> dict:
    """Apply non-secret immutable route configuration to the environment."""
    config = json.loads(Path(path).read_text())
    if config.get("route_id") != "qwen-official-payg-v1":
        raise ValueError("unsupported route_id")
    if canonical_model != config.get("canonical_model"):
        raise ValueError("route canonical_model does not match build model")
    if config.get("served_model") != QWEN_OFFICIAL_MODEL:
        raise ValueError("unexpected official served_model")
    os.environ["GUIEXP_MODEL_ROUTE"] = config["route_id"]
    os.environ["QWEN_OFFICIAL_BASE_URL"] = config["base_url"]
    rates = config["rates_cny_per_million"]
    os.environ["QWEN_OFFICIAL_INPUT_CNY_PER_M"] = str(rates["input"])
    os.environ["QWEN_OFFICIAL_OUTPUT_CNY_PER_M"] = str(rates["output"])
    os.environ["QWEN_OFFICIAL_CACHE_CNY_PER_M"] = str(rates["cache_read"])
    os.environ["QWEN_OFFICIAL_BUDGET_LEDGER"] = config["budget"]["ledger_path"]
    os.environ["QWEN_OFFICIAL_BUDGET_CAP_CNY"] = str(config["budget"]["cap_cny"])
    os.environ["QWEN_OFFICIAL_MAX_CALL_RESERVATION_CNY"] = str(
        config["budget"]["max_call_reservation_cny"])
    return config


def attach_official_accounting(record: dict) -> dict:
    """Attach deduplicated official receipts without changing frozen pw costs."""
    receipts = {}

    def visit(value):
        if isinstance(value, dict):
            receipt = value.get("route_receipt")
            if isinstance(receipt, dict) and receipt.get("route_id"):
                key = receipt.get("response_id") or f"anonymous-{len(receipts)}"
                receipts[key] = receipt
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(record)
    values = list(receipts.values())
    record["official_route_accounting"] = {
        "separate_from_frozen_pw": True,
        "calls": len(values),
        "prompt_tokens": sum(x["usage"]["prompt_tokens"] for x in values),
        "cached_tokens": sum(x["usage"]["cached_tokens"] for x in values),
        "completion_tokens": sum(x["usage"]["completion_tokens"] for x in values),
        "cost_cny": round(sum(x["cost_cny"] for x in values), 10),
        "receipts": values,
    }
    return record

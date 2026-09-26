"""V6 strict-action adapter. Compilation stays on inherited unstructured complete().

Pass the existing exclusive-run ledger with the original frozen quote pool.
This module never constructs a ledger and never retries a physical request.
"""
from __future__ import annotations
import copy
import json
import math
from . import recovery_validation_budget_v4 as base
from .recovery_validation_budget_v4 import (
    BudgetError, QuoteError, TransportUncertain, BudgetOverrun, BillingError,
    TARGET_PRICES, API_URL, _hash, _json, _validate_messages, _integer,
    _nanos, _load_api_key, _safe_request,
)

# Every action branch requires only arguments understood by the existing harness.
ARGUMENTS = {
    "click": {"index": {"type": "integer", "minimum": 0}},
    "long_press": {"index": {"type": "integer", "minimum": 0}},
    "input_text": {"index": {"type": "integer", "minimum": 0}, "text": {"type": "string"}, "clear_text": {"type": "boolean"}},
    "scroll": {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]}},
    "open_app": {"app_name": {"type": "string"}},
    "navigate_back": {}, "navigate_home": {}, "wait": {},
    "inspect_files": {"path": {"type": "string"}},
    "inspect_file": {"path": {"type": "string"}},
    "status": {"goal_status": {"type": "string", "enum": ["complete", "infeasible"]}},
}


def action_response_format():
    branches = []
    for kind, fields in ARGUMENTS.items():
        properties = {"action_type": {"type": "string", "enum": [kind]}, **copy.deepcopy(fields)}
        branches.append({"type": "object", "properties": properties,
                         "required": list(properties), "additionalProperties": False})
    return {"type": "json_schema", "json_schema": {"name": "android_action", "strict": True,
        "schema": {"type": "object", "properties": {"reason": {"type": "string"},
                    "action": {"anyOf": branches}}, "required": ["reason", "action"],
                   "additionalProperties": False}}}


def structured_endpoints(locks, model):
    """Filter only within the validated original pool, preserving its price vector."""
    required = {"response_format", "structured_outputs"}
    pool = [e for e in locks[model]["endpoints"]
            if required <= set(e.get("supported_parameters", []))]
    if not pool:
        raise QuoteError("No frozen endpoint advertises strict structured outputs")
    return copy.deepcopy(pool)


class ActionReplyError(BudgetError):
    """A settled, billed response failed the action protocol. No automatic retry."""
    def __init__(self, message, response):
        super().__init__(message)
        self.response = response
        self.generation_id = response.get("id")
        self.billed = True


def parse_action_reply(response):
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if choice.get("finish_reason") != "stop" or message.get("refusal"):
            raise ValueError("non-stop finish or refusal")
        value = json.loads(message["content"])
        if not isinstance(value, dict) or set(value) != {"reason", "action"} or not isinstance(value["reason"], str):
            raise ValueError("invalid envelope")
        action = value["action"]
        if not isinstance(action, dict) or action.get("action_type") not in ARGUMENTS:
            raise ValueError("unknown action")
        fields = ARGUMENTS[action["action_type"]]
        if set(action) != {"action_type", *fields}:
            raise ValueError("action arguments differ from schema")
        for key, rule in fields.items():
            item = action[key]
            types = {"integer": int, "string": str, "boolean": bool}
            if type(item) is not types[rule["type"]]:
                raise ValueError("argument type differs from schema")
            if "enum" in rule and item not in rule["enum"]:
                raise ValueError("invalid enum")
            if "minimum" in rule and item < rule["minimum"]:
                raise ValueError("negative index")
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise ActionReplyError("Billed action-schema failure: " + str(exc), response) from None


class StructuredActionClient(base.BudgetClient):
    def __init__(self, ledger, locks, env_path, transport=None, timeout=120):
        if ledger.budget_nusd != 30_000_000_000:
            raise BudgetError("V6 must reuse the original USD 30 budget")
        super().__init__(ledger, locks, env_path, transport, timeout)

    def complete_action(self, model, messages, max_tokens, episode, temperature=0, reasoning_effort="low"):
        response = self._complete_structured(model, messages, max_tokens, episode, temperature, reasoning_effort)
        providers = {e["provider_name"] for e in structured_endpoints(self.locks, model)}
        if response.get("provider") not in providers:
            raise ActionReplyError("Billed response provider outside structured subset", response)
        return {"response": response, "reply": parse_action_reply(response)}

    def _complete_structured(self, model: str, messages: list[dict[str, Any]], max_tokens: int,
                 episode: str, temperature: float = 0,
                 reasoning_effort: str = "low") -> dict[str, Any]:
        if _hash(_json(self.locks)) != self._locks_hash:
            raise QuoteError("The client's frozen endpoint metadata was modified")
        if model not in self.locks:
            raise QuoteError("Model is not in the frozen quote pool")
        _validate_messages(messages)
        max_tokens = _integer(max_tokens, "max_tokens", positive=True)
        if isinstance(temperature, bool) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise BudgetError("temperature must be finite and between 0 and 2")
        if reasoning_effort not in ("none", "minimal", "low", "medium", "high"):
            raise BudgetError("Unsupported reasoning_effort")
        lock = dict(self.locks[model], endpoints=structured_endpoints(self.locks, model))
        if max_tokens > lock["context_length"]:
            raise BudgetError("max_tokens exceeds the declared context bound")
        if any(endpoint.get("max_completion_tokens") is not None and
               max_tokens > endpoint["max_completion_tokens"] for endpoint in lock["endpoints"]):
            raise BudgetError("max_tokens exceeds an endpoint completion bound")
        prices = TARGET_PRICES[model]
        payload = {
            "model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "stream": False,
            "reasoning": {"effort": reasoning_effort},
            "usage": {"include": True},
            "response_format": action_response_format(),
            "provider": {
                "only": [endpoint["tag"] for endpoint in lock["endpoints"]],
                "allow_fallbacks": True, "require_parameters": True,
                "max_price": {**{key: float(prices[key] * 1_000_000)
                                 for key in ("prompt", "completion")}, "image": 0},
            },
        }
        serialized = _json(payload)
        reservation = _nanos(lock["context_length"] * prices["prompt"] +
                             max_tokens * prices["completion"], "reservation")
        api_key = _load_api_key(self.env_path)
        request_id = self.ledger.reserve(
            model=model, episode=episode, reservation_nusd=reservation,
            request_hash=_hash(serialized), request_json=_safe_request(payload))
        response = None
        try:
            response = self.transport(API_URL, payload, api_key, self.timeout)
            self.ledger.record_response(request_id, response)
        except BaseException as exc:
            self.ledger.fail(request_id, f"Uncertain transport or response: {type(exc).__name__}")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise TransportUncertain("Request outcome is uncertain. Reservation retained and paid work stopped.") from None
        finally:
            api_key = ""
        try:
            fields = self._billing_fields(model, response, max_tokens)
        except (BudgetError, ValueError, TypeError, KeyError) as exc:
            reason = str(exc) if isinstance(exc, BudgetError) else "Malformed response or billing"
            state = self.ledger.fail(request_id, reason, response)
            if state == "overrun":
                raise BudgetOverrun("Reported cost exceeds the reservation. Paid work stopped.") from None
            raise BillingError(f"{reason}. Reservation retained and paid work stopped.") from None
        self.ledger.settle(request_id, fields)
        return response


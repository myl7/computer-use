#!/usr/bin/env python3
"""Operator-confirmed bounded recovery of unresolved official Qwen billing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--cap-cny", required=True)
    parser.add_argument("--confirmed-billing-unresolved", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo / "code"))
    from guiexp_android.official_qwen_client import recover_shared_budget_ledger

    record = recover_shared_budget_ledger(
        args.ledger, args.cap_cny, args.confirmed_billing_unresolved)
    print(json.dumps({"blocked": record["blocked"],
                      "spent_cny": record["spent_cny"],
                      "reserved_cny": record["reserved_cny"],
                      "unresolved_calls": record["unresolved_calls"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

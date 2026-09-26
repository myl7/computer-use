"""Audit admitted deployment means from actual extraction token ledgers."""
import hashlib
import json
from pathlib import Path

from normalize_results import normalize
from validate_results import ROOT, expected_main

HERE = Path(__file__).resolve().parent


def main():
    manifest = json.loads((HERE / "evidence-selection.json").read_text())
    profiles = json.loads((HERE / "profiles.json").read_text())
    rows = []
    for selected in manifest["selections"]:
        identity = selected["canonical_identity"]
        key = (identity["platform"], identity["model"], identity["family"])
        if identity["attempt_role"] != "initial" or identity["attempt_id"] != "initial" or key not in expected_main():
            continue
        record = normalize(json.loads((ROOT / selected["raw_path"]).read_text()))
        if not record.get("admission"):
            continue
        metrics = record.get("profile_metrics")
        if not metrics or metrics.get("uses") != 30:
            raise ValueError(f"Missing 30-use token-ledger metrics: {key}")
        name = identity["platform"].title() + "/" + identity["family"]
        profile_d = profiles["models"][identity["model"]][name]["revised"]["d"]
        if abs(profile_d - metrics["d"]) > 1e-8:
            raise ValueError(f"Profile/deployment d mismatch: {key}")
        rows.append({"platform": identity["platform"], "model": identity["model"],
                     "family": identity["family"], "deployment_status": record["deployment"]["status"],
                     "uses": 30, "failures": metrics["failures"], "d_pw": metrics["d"],
                     "q": metrics["q"], "source_result": selected["raw_path"],
                     "source_result_sha256": selected["raw_sha256"]})
    if len(rows) != 11:
        raise ValueError(f"Expected 11 admitted deployment audits, got {len(rows)}")
    output = {"schema": "deployment-frozen-pw-audit/1", "status": "verified",
              "price_sheet": "measurement_update_20260918-prices-v1",
              "rule": "For both reused and new deployments, d is the mean frozen-pw extraction charge recomputed from each retained call's prompt/cached/completion counts; cached tokens must be a subset of prompt tokens.",
              "profiles_sha256": hashlib.sha256((HERE / "profiles.json").read_bytes()).hexdigest(),
              "cells": rows}
    (HERE / "deployment-pw-audit.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cells": len(rows), "d_range": [min(r["d_pw"] for r in rows), max(r["d_pw"] for r in rows)]}))


if __name__ == "__main__": main()

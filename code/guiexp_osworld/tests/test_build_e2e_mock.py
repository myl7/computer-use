"""Zero-LLM end-to-end run of the whole build protocol (mock model, real
guest). Reduced counts (floor 3, deploy 5) to keep wall time sane; every
stage's logic, record shape and accounting path is exercised exactly as the
paid run will be.
"""

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from guiexp_osworld import guest_env  # noqa: E402
from guiexp_osworld.build_protocol import run_build  # noqa: E402
from guiexp_osworld.mock_model import MockOSWorld  # noqa: E402


def main() -> int:
    from guiexp_osworld.mock_model import _ScriptedCompletions
    from guiexp_osworld.tests.golden_programs import GOLDEN_CALC, GOLDEN_WRITER

    family = sys.argv[1] if len(sys.argv) > 1 else "CalcTableSave"
    _ScriptedCompletions.GOLDEN = GOLDEN_CALC if family == "CalcTableSave" else GOLDEN_WRITER

    out = Path("../experimental-results/guiexp_osworld/mock_e2e") / family
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    client = MockOSWorld()
    env = guest_env.OSWorldEnv(start_if_needed=False)
    t0 = time.time()
    record = run_build(
        family=family, model="mock", out_dir=out, env=env,
        deploy_n=5, client=client,
    )
    record["_note"] = "MOCK E2E (reduced counts); not a measurement"
    (out / "build.json").write_text(json.dumps(record, indent=1, default=str))
    env.close()

    be = record["break_even"]
    print(json.dumps({
        "family": family,
        "wall_s": round(time.time() - t0, 1),
        "floor_unit": be["floor_unit"],
        "c_unit_floored": be["c_unit_floored"],
        "d_unit": be["d_unit"],
        "q": be["q"],
        "s_unit": be.get("s_unit"),
        "nstar": be.get("nstar"),
        "deploy": {k: record["deploy"].get(k) for k in ("n", "success_count", "success_rate")},
        "doc_arm": record["doc_arm"]["success_count"],
        "total_cost_usd": record["total_cost_usd"],
    }, indent=1))
    ok = (
        record["deploy"]["success_count"] == record["deploy"]["n"]
        and record["doc_arm"]["success_count"] == len(record["doc_arm"]["episodes"])
        and all(i["success"] for i in record["exploration"]["instances"])
        and be.get("s_unit", 0) > 0
    )
    print("E2E", "OK" if ok else "PROBLEM")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

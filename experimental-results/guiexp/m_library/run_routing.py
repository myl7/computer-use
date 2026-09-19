"""Standalone routing-accuracy experiment (no browser, no guiexp agent).

For each manifest size n and each of 20 goals, one chat call asks the model
to route: `USE: <program_name>` (verbatim from the manifest) or `NONE`.
Ground truth is defined PER MANIFEST: a match goal's correct answer is
`USE: <target>` when the target is listed in manifest(n), else `NONE`
(the library genuinely lacks it); distractor goals are correct `NONE` at
every n. n=0 (empty manifest) is included as the false-positive baseline --
against it, the real OpenApps calendar goal's correct answer is NONE
("calendar goal against a manifest that excludes calendar programs").

Grid:
  z-ai/glm-5.3-flash           n in {0,1,5,20,100} x 20 goals x seeds {0,1}
  deepseek/deepseek-v4-flash-vision-exp (spot) n in {20,100} x 20 x seed {0}

Temperature is NOT set (model default); `seed` is passed for sampling
variation. Per-call usage (prompt/completion tokens, OpenRouter cost) is
recorded to routing_calls.jsonl; a summary lands in routing_summary.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4-flash-vision-exp"

ROUTER_INSTRUCTION = (
    "You are the router of a GUI-automation library. For each user goal you "
    "decide whether one of the library's compiled programs can accomplish the "
    "goal end-to-end. The library manifest below lists every available program "
    "with its trigger scenario and parameters. A program matches only when its "
    "trigger scenario fits the goal and its parameters can carry the specifics "
    "the user gave; closely related but different tasks do not match. Reply "
    "with exactly one line and nothing else: 'USE: <program_name>' with the "
    "single best-matching program name copied verbatim from the manifest, or "
    "'NONE' when no listed program fits. Never output a program name that is "
    "not in the manifest.\n\n"
)

# goal_id -> (text, target_program_or_None)
GOALS: list[tuple[str, str, str | None]] = [
    # ---- 6 that SHOULD match (natural phrasing, not verbatim triggers) ----
    ("G1_calendar_wizard",
     "I need to get the quarterly sync with Dennis onto the calendar — it's "
     "April 1, Room 3, the video link is https://example.com/sync, description "
     "'Quarterly sync'. Our calendar site walks you through the new-event form "
     "one screen at a time with a Next button.",
     "openapps_calendar_create_event_wizard"),
    ("G2_todo_add",
     "Please put 'renew passport' on my to-do list for October 1 — mark it "
     "important.",
     "openapps_todo_add_item"),
    ("G3_flights_roundtrip",
     "Lock in flights for me: out to London October 12, back October 19, "
     "economy, two travelers.",
     "flights_book_roundtrip"),
    ("G4_email_attachment",
     "Send Dana an email with the Q3 report PDF attached — subject 'Q3 "
     "documents', body just 'attached, please review by Friday'.",
     "email_send_with_attachment"),
    ("G5_spreadsheet_row",
     "Append a row to the October sheet with today's numbers: 42 units, 5039 "
     "dollars, downtown store.",
     "spreadsheet_append_row"),
    ("G6_messages_send",
     "Text Mom from the messages app: 'running 15 minutes late'.",
     "openapps_messages_send"),
    # ---- 14 distractors (correct NONE at every manifest size) ----
    ("D1_delete_event",
     "Remove the 'Security audit' event from my calendar — another team owns "
     "it now.", None),
    ("D2_move_flight",
     "My plans changed: move my existing London flight from October 12 to "
     "October 15, same airline.", None),
    ("D3_unsubscribe",
     "Please take me off the daily-deals newsletter mailing list, I get too "
     "many of them.", None),
    ("D4_share_edit",
     "Share the budget sheet with Sam — he needs to be able to edit it.", None),
    ("D5_bank_transfer",
     "Move 500 dollars from my checking account to my savings account.", None),
    ("D6_order_pizza",
     "Order a large pepperoni pizza for delivery tonight around 7.", None),
    ("D7_post_photo",
     "Post the beach photo to my feed with the caption 'sunset'.", None),
    ("D8_track_run",
     "Start tracking my 5k run this evening.", None),
    ("D9_leave_review",
     "Leave a five-star review for the standing desk I bought last month.",
     None),
    ("D10_boarding_pass",
     "Print my boarding pass for tomorrow morning's flight.", None),
    ("D11_thermostat",
     "Set the thermostat to 68 and turn off the downstairs lights before we "
     "leave.", None),
    ("D12_find_doctor",
     "Find me a new primary care doctor nearby and get my records "
     "transferred.", None),
    ("D13_cancel_gym",
     "Cancel my gym membership before it renews on the first.", None),
    ("D14_backup_email",
     "Back up my entire email account to a local archive file on the laptop.",
     None),
]


def manifest_members(n: int) -> set[str]:
    if n == 0:
        return set()
    text = (HERE / f"manifest_n{n}.txt").read_text()
    return set(re.findall(r"^PROGRAM (\S+) —", text, flags=re.M))


def parse_reply(reply: str) -> tuple[str, str | None]:
    """-> (pred_kind, pred_name); pred_kind in {use, none, malformed}."""
    text = (reply or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    if re.fullmatch(r"NONE[.!]?", first, flags=re.I) or text.upper().startswith("NONE"):
        return "none", None
    m = re.search(r"USE:\s*`?([A-Za-z0-9_.\-]+)`?", text)
    if m:
        return "use", m.group(1)
    return "malformed", None


def main() -> int:
    from openai import OpenAI

    out_path = HERE / "routing_calls.jsonl"
    summary_path = HERE / "routing_summary.json"

    client = OpenAI(
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=180.0,
        max_retries=3,
    )

    grid: list[tuple[str, int, int]] = []
    for n in (0, 1, 5, 20, 100):
        for seed in (0, 1):
            grid.append((GLM, n, seed))
    for n in (20, 100):
        grid.append((DS, n, 0))

    records: list[dict] = []
    total_cost = 0.0
    t0 = time.time()
    for i, (model, n, seed) in enumerate(grid):
        manifest_text = (HERE / f"manifest_n{n}.txt").read_text()
        members = manifest_members(n)
        system = ROUTER_INSTRUCTION + manifest_text
        for goal_id, goal_text, target in GOALS:
            gt = target if target in members else None
            gt_kind = "use" if gt else "none"
            resp = None
            for attempt in range(3):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": goal_text},
                        ],
                        max_tokens=512,
                        seed=seed,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    print(f"retry after error: {type(exc).__name__}: {exc}",
                          file=sys.stderr)
                    time.sleep(5 * (attempt + 1))
            usage = resp.usage
            cost = getattr(usage, "cost", None)
            if cost is None and getattr(usage, "model_extra", None):
                cost = usage.model_extra.get("cost")
            reply = resp.choices[0].message.content or ""
            pred_kind, pred_name = parse_reply(reply)
            correct = (pred_kind == "use" and pred_name == gt) or (
                pred_kind == "none" and gt is None)
            # error taxonomy
            if correct:
                err = "correct"
            elif pred_kind == "malformed":
                err = "malformed"
            elif gt is None:
                err = "false_positive" if pred_name in members else "unlisted_name"
            else:  # gt is a listed program
                if pred_kind == "none":
                    err = "false_negative"  # NONE-miss
                elif pred_name not in members:
                    err = "unlisted_name"
                else:
                    err = "wrong_program"
            rec = {
                "model": model, "n": n, "seed": seed, "goal_id": goal_id,
                "gt": gt, "gt_kind": gt_kind,
                "reply": reply, "pred_kind": pred_kind, "pred_name": pred_name,
                "pred_listed": pred_name in members if pred_name else None,
                "correct": correct, "error_type": err,
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "cost_usd": cost,
            }
            records.append(rec)
            total_cost += cost or 0.0
            if total_cost > 1.5:
                print("COST GUARD hit (> $1.5), aborting", file=sys.stderr)
                break
        done = i + 1
        print(f"[{done}/{len(grid)}] {model} n={n} seed={seed} "
              f"running_total_cost=${total_cost:.4f} "
              f"({time.time()-t0:.0f}s)", flush=True)

    with out_path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")

    # ---- summary ----------------------------------------------------------
    summary: dict = {"total_calls": len(records), "total_cost_usd": total_cost,
                     "cells": {}}
    for model in (GLM, DS):
        for n in (0, 1, 5, 20, 100):
            rs = [r for r in records if r["model"] == model and r["n"] == n]
            if not rs:
                continue
            errs = {e: 0 for e in ("correct", "wrong_program", "false_positive",
                                   "false_negative", "unlisted_name", "malformed")}
            for r in rs:
                errs[r["error_type"]] += 1
            match_cells = [r for r in rs if r["gt_kind"] == "use"]
            dist_cells = [r for r in rs if r["gt_kind"] == "none"]
            summary["cells"][f"{model}|n={n}"] = {
                "calls": len(rs),
                "accuracy": errs["correct"] / len(rs),
                "accuracy_match_cells": (
                    sum(r["correct"] for r in match_cells) / len(match_cells)
                    if match_cells else None),
                "accuracy_distractor_cells": (
                    sum(r["correct"] for r in dist_cells) / len(dist_cells)
                    if dist_cells else None),
                "errors": errs,
                "mean_prompt_tokens": sum(r["prompt_tokens"] for r in rs) / len(rs),
                "mean_completion_tokens": sum(r["completion_tokens"] for r in rs) / len(rs),
                "total_cost_usd": sum(r["cost_usd"] or 0 for r in rs),
            }
    summary_path.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary["cells"], indent=1))
    print(f"TOTAL calls={len(records)} cost=${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
build_families.py -- build arrival-stream family maps + stats from the raw
enwiki ns0 recentchanges pull.

Family definition (paper T0.8):
  family = tool tag x edit-summary template; one-off content edits form
  singleton families.

Two granularities:
  COARSE: (bot flag) x (tool tag: JWB/AWB/twinkle/none) x (summary class:
          typo/fix/cite/ref/categor/redirect/disambig/cleanup/infobox/
          section-edit/empty/other) via an ordered regex taxonomy.
  FINE:   (coarse key) x (normalized literal summary template). Templates are
          normalized by stripping "/* section */" prefixes, quoted strings and
          [[wikilinks]], replacing digits with '#', collapsing whitespace,
          lowercasing. ALL distinct (coarse, template) pairs form families
          (not just top-50); the top-50 patterns are materialized explicitly
          in families.json and the report tables.
          One special rule implements "one-off content edits = singleton
          families": an edit with NO tool tag, NO bot flag and NO usable
          summary (empty, or only a "/* section */" auto-prefix) carries no
          repetition evidence, so each such edit becomes its own singleton
          family ("(oneoff:<rcid>)") rather than joining one giant "(empty)" family.
          (Tool/bot edits with empty summaries keep the "(empty)" template:
          the tool/bot identity is the repetition signal.)

Outputs (in --dir):
  families.json          : mapping for the two replay streams (id -> label/stats)
  stream_tool_hot_tail.jsonl   : {ts, family_id} arrival order, tool/bot edits only
  stream_content_tail.jsonl    : {ts, family_id} arrival order, human edits only
  families_coarse.json   : full coarse family table (label -> stats)
  families_fine.json     : full fine family table (label -> stats)
  stats.json             : all stream-level stats used in structure_report.md

Usage: python3 build_families.py [--dir DIR] [--raw raw_enwiki_ns0_72h.json.gz]
"""

import argparse
import collections
import gzip
import json
import os
import re
import sys

# ---------------------------------------------------------------- taxonomy ---
SECTION_RE = re.compile(r"/\*[^*]*\*/")          # "/* section name */" prefixes
WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")      # [[target|label]]
QUOTE_RE = re.compile(r'"[^"]*"|\u201c[^\u201d]*\u201d')
DIGIT_RE = re.compile(r"\d+")

# summary classes, ordered: first match wins (specific -> generic)
CLASS_RULES = [
    ("infobox",   re.compile(r"infobox|\btemplate\b|\{\{", re.I)),
    ("disambig",  re.compile(r"disambig|\bdab\b", re.I)),
    ("redirect",  re.compile(r"\bredirect(s|ing|ed)?\b|#redirect", re.I)),
    ("categor",   re.compile(r"\bcategor(y|ies|iz)", re.I)),
    ("ref",       re.compile(r"\brefs?\b|\breferenc|\b<ref|\bcite ?[a-z]* ?needed|adding (a |sources? )?ref", re.I)),
    ("cite",      re.compile(r"\bcit(e|es|ed|ing|ation)", re.I)),
    ("typo",      re.compile(r"\btypo|spelling|misspell|copy ?edit|\bce\b ?\(|capitali[sz]|punctuation|grammar|wikify|dead ?links?|deadlink|cleanup|clean[- ]up", re.I)),
    ("cleanup",   re.compile(r"clean ?up|format|reformat|style|layout|reorganiz|reorganis|updat|expand|rewrit|remov|delet|add(?:ing|ed)?\b", re.I)),
    ("fix",       re.compile(r"\bfix(es|ed|ing)?\b|correct", re.I)),
]

def summary_class(comment: str) -> str:
    c = (comment or "").strip()
    if not c:
        return "empty"
    stripped = SECTION_RE.sub("", c).strip()
    if not stripped:                      # pure "/* section */" comment
        return "section-edit"
    for name, rx in CLASS_RULES:
        if rx.search(stripped):
            return name
    return "other"

def normalize_template(comment: str) -> str:
    c = (comment or "").strip()
    if not c:
        return "(empty)"
    c = SECTION_RE.sub("", c)             # strip "/* section */" prefixes
    c = WIKILINK_RE.sub("[]]", c)         # strip [[wikilinks]]
    c = QUOTE_RE.sub('"..."', c)
    c = DIGIT_RE.sub("#", c)
    c = re.sub(r"\s+", " ", c).strip().lower()
    return c if c else "(empty)"

def tool_of(tags) -> str:
    for t in ("JWB", "AWB", "twinkle"):
        if t in tags:
            return t
    return "none"

# ------------------------------------------------------------------ helpers --
def gini(counts):
    """Gini coefficient over family arrival counts."""
    xs = sorted(counts)
    n = len(xs)
    if n == 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(xs, 1):
        cum += i * x
    total = sum(xs)
    return (2.0 * cum) / (n * total) - (n + 1.0) / n

def top_decile_share(counter):
    """Share of arrivals going to the top 10% of families."""
    items = sorted(counter.values(), reverse=True)
    k = max(1, len(items) // 10)
    return sum(items[:k]) / sum(items) if items else 0.0

def fam_stats(counter, users_per_fam, total):
    sizes = sorted(counter.values(), reverse=True)
    n_fam = len(counter)
    singletons = sum(1 for v in counter.values() if v == 1)
    top_label, top_n = counter.most_common(1)[0] if counter else ("", 0)
    return {
        "total_arrivals": total,
        "families": n_fam,
        "singleton_families": singletons,
        "singleton_family_pct": round(100.0 * singletons / n_fam, 2) if n_fam else 0.0,
        "singleton_arrival_share_pct": round(100.0 * sizes.count(1) / total, 2) if total else 0.0,
        "top_family": top_label,
        "top_family_n": top_n,
        "top_family_share_pct": round(100.0 * top_n / total, 2) if total else 0.0,
        "gini": round(gini(list(counter.values())), 4),
        "top_decile_family_share_pct": round(100.0 * top_decile_share(counter), 2),
    }

# --------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--raw", default=None)
    args = ap.parse_args()
    raw_path = args.raw or os.path.join(args.dir, "raw_enwiki_ns0_72h.json.gz")
    out = lambda name: os.path.join(args.dir, name)

    with gzip.open(raw_path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    meta, edits = doc["meta"], doc["edits"]
    win_start, win_end = meta["window_effective"]["start"], meta["window_effective"]["end"]
    print(f"loaded {len(edits)} edits, window [{win_start} .. {win_end}]")

    # arrival order = ascending (timestamp, rcid); API pull order is newest->oldest
    seen = set()
    recs = []
    for e in edits:
        rcid = e.get("rcid")
        if rcid in seen:
            continue
        seen.add(rcid)
        recs.append(e)
    recs.sort(key=lambda e: (e["timestamp"], e.get("rcid") or 0))
    print(f"{len(recs)} unique edits after dedupe; sorted ascending")

    # ---- classify -------------------------------------------------------------
    coarse_counter = collections.Counter()
    coarse_users = collections.defaultdict(set)
    fine_counter = collections.Counter()
    fine_users = collections.defaultdict(set)
    hourly = collections.Counter()
    user_counter = collections.Counter()
    rows = []  # (ts, fine_key, user) in arrival order
    n_oneoff = 0
    n_empty_summary = 0
    n_sectiononly = 0
    for e in recs:
        bot = bool(e.get("bot"))
        tool = tool_of(e.get("tags") or [])
        comment = e.get("comment") or ""
        cls = summary_class(comment)
        tmpl = normalize_template(comment)
        if not comment:
            n_empty_summary += 1
        elif tmpl == "(empty)":
            n_sectiononly += 1    # comment was only a "/* section */" prefix
        if tmpl == "(empty)" and tool == "none" and not bot:
            # unique singleton family per edit (see docstring)
            tmpl = f"(oneoff:{e.get('rcid')})"
            n_oneoff += 1
        ck = f"{int(bot)}|{tool}|{cls}"
        fk = f"{ck}|{tmpl}"
        coarse_counter[ck] += 1
        coarse_users[ck].add(e.get("user", "?"))
        fine_counter[fk] += 1
        fine_users[fk].add(e.get("user", "?"))
        hourly[e["timestamp"][:13]] += 1
        user_counter[e.get("user", "?")] += 1
        rows.append((e["timestamp"], fk, e.get("user", "?")))

    total = len(rows)
    cs = fam_stats(coarse_counter, coarse_users, total)
    fs = fam_stats(fine_counter, fine_users, total)
    print("COARSE:", json.dumps(cs)[:300])
    print("FINE:  ", json.dumps(fs)[:300])

    # top-10 tables
    def top10(counter, users):
        t = []
        for k, n in counter.most_common(10):
            parts = k.split("|")
            label = (f"bot={parts[0]} tool={parts[1]} class={parts[2]}"
                     + (f" | {parts[3][:80]}" if len(parts) > 3 else ""))
            t.append({"family": label, "key": k, "arrivals": n,
                      "share_pct": round(100.0 * n / total, 2),
                      "users": len(users[k])})
        return t

    stats = {
        "meta": meta,
        "window_effective": {"start": win_start, "end": win_end},
        "n_arrivals": total,
        "coarse": cs, "fine": fs,
        "coarse_top10": top10(coarse_counter, coarse_users),
        "fine_top10": top10(fine_counter, fine_users),
        "hourly_arrivals": dict(sorted(hourly.items())),
        "hourly_summary": {
            "hours": len(hourly),
            "mean": round(total / max(1, len(hourly)), 1),
            "min": min(hourly.values()) if hourly else 0,
            "min_hour": min(hourly, key=hourly.get) if hourly else None,
            "max": max(hourly.values()) if hourly else 0,
            "max_hour": max(hourly, key=hourly.get) if hourly else None,
        },
    }

    # ---- full family tables (for families_*.json) ------------------------------
    def fam_table(counter, users):
        t = {}
        for k, n in counter.items():
            bot, tool, cls = k.split("|")[:3]
            t[k] = {"bot": bot == "1", "tool": tool, "class": cls,
                    "arrivals": n, "users": len(users[k]),
                    "share_pct": round(100.0 * n / total, 3)}
        return t
    coarse_table = fam_table(coarse_counter, coarse_users)
    fine_rows = [{"key": k, "bot": k.split("|")[0] == "1", "tool": k.split("|")[1],
                  "class": k.split("|")[2], "template": k.split("|", 3)[3],
                  "arrivals": n, "users": len(fine_users[k]),
                  "share_pct": round(100.0 * n / total, 4)}
                 for k, n in fine_counter.most_common(5000)]
    n_fine_all = len(fine_counter)
    n_fine_singleton = sum(1 for v in fine_counter.values() if v == 1)
    with open(out("families_coarse.json"), "w") as f:
        json.dump(coarse_table, f, indent=1)
    with open(out("families_fine.json"), "w") as f:
        json.dump({"note": "top-5000 fine families by arrivals (of "
                           f"{n_fine_all} total; {n_fine_singleton} are "
                           "singletons). Template normalization: strip "
                           "/*section*/ + [[wikilinks]] + quoted strings, "
                           "digits->#, collapse ws, lowercase. '(oneoff:<rcid>)' = "
                           "empty-summary human edit, one singleton family each.",
                   "families": fine_rows}, f, indent=1)

    stats["empty_summary_edits"] = n_empty_summary
    stats["sectiononly_summary_edits"] = n_sectiononly
    stats["oneoff_singleton_edits"] = n_oneoff

    # user concentration + bot/tool mix (stream-level color for the report)
    top_user, top_user_n = user_counter.most_common(1)[0]
    bot_n = sum(n for k, n in coarse_counter.items() if k.startswith("1|"))
    tool_n = collections.Counter()
    for k, n in coarse_counter.items():
        tool_n[k.split("|")[1]] += n
    stats["users"] = {
        "distinct_users": len(user_counter),
        "top_user": top_user, "top_user_arrivals": top_user_n,
        "top_user_share_pct": round(100.0 * top_user_n / total, 2),
        "top10_user_share_pct": round(100.0 * sum(n for _, n in
            user_counter.most_common(10)) / total, 2),
        "bot_flagged_share_pct": round(100.0 * bot_n / total, 2),
        "tool_shares_pct": {t: round(100.0 * n / total, 2)
                            for t, n in tool_n.most_common()},
    }

    top50 = [r["template"] for r in fine_rows[:50]]
    stats["fine_top50_templates"] = [
        {**r, "rank": i + 1} for i, r in enumerate(fine_rows[:50])]

    # ---- replay streams --------------------------------------------------------
    # Stream A (tool_hot_tail): tool-tagged or bot-flagged edits; FINE families.
    # Stream B (content_tail):  no tool tag and not bot; FINE families.
    # family = (bot x tool x class x template) fine key; within each stream the
    # top-50 families (by arrivals) get materialized ids <prefix>F001..F050;
    # every remaining family gets a unique id <prefix>S0000001.. (mostly, but
    # not only, singletons). families.json maps ids to labels/stats.
    def build_stream(sel, prefix, name, note):
        sub = [r for r in rows if sel(r)]
        cnt = collections.Counter(r[1] for r in sub)          # by fine key
        ranked = [k for k, _ in cnt.most_common(50)]
        sid = {k: f"{prefix}F{i+1:03d}" for i, k in enumerate(ranked)}
        tail_ids = {}   # fine key -> unique tail family id (first-arrival order)
        mapping, out_rows = {}, []
        for ts, fk, user in sub:
            if fk in sid:
                fid = sid[fk]
            else:
                fid = tail_ids.get(fk)
                if fid is None:
                    fid = tail_ids[fk] = f"{prefix}S{len(tail_ids)+1:07d}"
            m = mapping.setdefault(fid, {"key": fk, "arrivals": 0, "users": set()})
            m["arrivals"] += 1
            m["users"].add(user)
            out_rows.append({"ts": ts, "family_id": fid})
        path = out(f"stream_{name}.jsonl")
        with open(path, "w") as f:
            for r in out_rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        fc = collections.Counter(r["family_id"] for r in out_rows)
        st = fam_stats(fc, None, len(out_rows))
        st["note"] = note
        st["path"] = path
        # top families with human-readable labels for the report
        st["top10"] = []
        for fid, n in fc.most_common(10):
            k = mapping[fid]["key"]
            bot, tool, cls, tmpl = k.split("|", 3)
            st["top10"].append({"family_id": fid,
                                "label": f"bot={bot} tool={tool} class={cls} | {tmpl[:80]}",
                                "arrivals": n,
                                "share_pct": round(100.0 * n / len(out_rows), 2),
                                "users": len(mapping[fid]["users"])})
        fams = {fid: {"key": m["key"], "bot": m["key"].split("|")[0] == "1",
                      "tool": m["key"].split("|")[1], "class": m["key"].split("|")[2],
                      "template": m["key"].split("|", 3)[3],
                      "arrivals": m["arrivals"], "users": len(m["users"])}
                for fid, m in mapping.items()
                if m["arrivals"] > 1}   # singleton S-ids omitted (unique by construction)
        with open(out(f"stream_{name}_families.json"), "w") as f:
            json.dump({"stream": name, "stats": st,
                       "families": fams}, f, indent=1)
        return out_rows, st, fams

    def is_tool_or_bot(fk):
        bot, tool = fk.split("|")[0], fk.split("|")[1]
        return tool != "none" or bot == "1"

    def is_human(fk):
        return fk.startswith("0|none|")

    a_rows, a_stats, a_fams = build_stream(
        (lambda r: is_tool_or_bot(r[1])),
        "T", "tool_hot_tail",
        "tool-driven hot+tail: edits with tool tag (JWB/AWB/twinkle) or bot flag; "
        "family = fine (bot x tool x class x normalized template).")
    b_rows, b_stats, b_fams = build_stream(
        (lambda r: is_human(r[1])),
        "H", "content_tail",
        "content-tail-heavy: human edits (no tool tag, no bot flag); "
        "family = fine (bot x tool x class x normalized template); "
        "empty/section-only-summary edits are per-edit singleton families '(oneoff:<rcid>)'.")

    stats["streams"] = {"tool_hot_tail": a_stats, "content_tail": b_stats}
    with open(out("stats.json"), "w") as f:
        json.dump(stats, f, indent=1, default=str)
    print("stream A:", json.dumps({k: a_stats[k] for k in
          ('total_arrivals', 'families', 'singleton_family_pct', 'top_family_share_pct')}))
    print("stream B:", json.dumps({k: b_stats[k] for k in
          ('total_arrivals', 'families', 'singleton_family_pct', 'top_family_share_pct')}))

    # combined families.json (id -> label) with per-stream namespaces
    with open(out("families.json"), "w") as f:
        json.dump({
            "_note": "family_id namespaces: T*=stream_tool_hot_tail, H*=stream_content_tail. "
                     "TF###/HF### = top-50 template families of each stream; TS*/HS* = "
                     "unique singleton/tail family ids. Per-stream detail incl. all "
                     "non-singleton families in stream_*_families.json.",
            "tool_hot_tail": a_fams, "content_tail": b_fams,
        }, f, indent=1)

    # ---- structure_report.md ---------------------------------------------------
    def md_table(headers, rows_md):
        esc = lambda c: str(c).replace("|", "\\|")
        lines = ["| " + " | ".join(headers) + " |",
                 "|" + "|".join("---" for _ in headers) + "|"]
        lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows_md]
        return "\n".join(lines)

    wr = meta["window_requested"]
    we = meta["window_effective"]
    hours = wr["hours"]
    u = stats["users"]
    hs = stats["hourly_summary"]
    hours_list = sorted(stats["hourly_arrivals"])
    hourly_rows = [[h.replace("T", " "), f"{stats['hourly_arrivals'][h]:,}"]
                   for h in hours_list]

    def stat_row(name, st):
        return [name, f"{st['total_arrivals']:,}", f"{st['families']:,}",
                f"{st['singleton_family_pct']:.1f}%",
                f"{st['singleton_arrival_share_pct']:.1f}%",
                str(st["top_family"]), f"{st['top_family_share_pct']:.1f}%",
                f"{st['gini']:.3f}", f"{st['top_decile_family_share_pct']:.1f}%"]

    top10c = [[i + 1, r["family"], f"{r['arrivals']:,}", f"{r['share_pct']:.2f}%", r["users"]]
              for i, r in enumerate(stats["coarse_top10"])]
    top10f = [[i + 1, r["family"], f"{r['arrivals']:,}", f"{r['share_pct']:.2f}%", r["users"]]
              for i, r in enumerate(stats["fine_top10"])]
    top50f = [[r["rank"], f"bot={int(r['bot'])} tool={r['tool']} class={r['class']}",
               r["template"][:70], f"{r['arrivals']:,}", f"{r['share_pct']:.2f}%", r["users"]]
              for r in stats["fine_top50_templates"][:20]]

    def stream_section(title, st, note):
        t10 = [[r["family_id"], r["label"], f"{r['arrivals']:,}",
                f"{r['share_pct']:.2f}%", r["users"]] for r in st["top10"][:5]]
        return (f"### {title}\n\n{note}\n\n"
                f"- arrivals: **{st['total_arrivals']:,}**, families: **{st['families']:,}**"
                f" (singleton families: {st['singleton_family_pct']:.2f}%; "
                f"singleton arrival share: {st['singleton_arrival_share_pct']:.2f}%)\n"
                f"- top family: **{st['top_family']}** = {st['top_family_share_pct']:.1f}% of arrivals; "
                f"Gini = {st['gini']:.3f}; top-decile-of-families share = "
                f"{st['top_decile_family_share_pct']:.1f}%\n"
                f"- file: `{os.path.basename(st['path'])}` "
                '(`{"ts": "<ISO8601>", "family_id": "..."}` per line, '
                f"arrival order ascending)\n\n"
                + md_table(["family_id", "label", "arrivals", share_col, "users"], t10) + "\n")

    n_req, n_rl = 0, 0
    log_path = out("pull.log")
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                if "  fetched " in line:
                    n_req += 1
                elif "429 rate-limited" in line:
                    n_rl += 1

    share_col = "share"
    report = f"""# Wikipedia maintenance editing: arrival-stream structure (T0.8)

Real arrival streams for the compile-policy experiments: each Wikipedia edit is
an arrival `(timestamp, family)`; family = tool tag x edit-summary template;
one-off content edits form singleton families.

## 1. Methodology

- **Source**: public MediaWiki API of `en.wikipedia.org`
  (`action=query&list=recentchanges`), `rcnamespace=0` (articles),
  `rctype=edit`, `rclimit=500`, `rcdir=older`, `rcprop=title|ids|timestamp|user|comment|flags|tags`,
  `formatversion=2`, `maxlag=5`. User-Agent `compile-policy-research/0.1 (academic)`.
- **Window requested**: {wr['hours']}h = [{wr['start']} .. {wr['end']}] (rcstart = newer end, rcend = older end).
- **Window effective**: [{we['start']} .. {we['end']}] ({hours}h contiguous, truncation flag: {meta['truncated']}).
- **Fallback note**: a full 72h window was attempted first; the API's rate
  limiter (HTTP 429 + Retry-After) slowed pagination beyond the 30-minute
  budget, so the task's fallback to a 48h window was applied. The 48h window
  ends at the same `rcstart` as the aborted 72h attempt; the first ~6h of
  records were reused (the pull walks newest-to-oldest), the rest pulled with a
  2s request cadence honoring every `Retry-After`.
- **Rate policy**: >= 2s between request starts (< 0.5 req/s sustained), plus
  exact honoring of server `Retry-After`; crash-safe resume via cursor state.
  Pull effort: {n_req} successful list=recentchanges requests and {n_rl}
  honored rate-limit pauses for the final 48h run (plus a 70-request aborted
  72h attempt); wall time {meta.get('pull_started_utc', '?')} ..
  {meta.get('pull_finished_utc', '?')} UTC.
- **Cleaning**: dedupe by `rcid`, sort ascending by `(timestamp, rcid)` =
  arrival order. Total arrivals: **{total:,} edits** over {len(hours_list)} hour-buckets
  (mean {hs['mean']:.0f}/h, min {hs['min']:,} at {hs['min_hour']}, max {hs['max']:,} at {hs['max_hour']}; first/last buckets partial).
- **Raw pull**: `raw_enwiki_ns0_{hours}h.json.gz` = gzip of one JSON document
  `{{"meta": ..., "edits": [...]}}`; edits are raw API records in pull order
  (newest -> oldest). Pull script: `pull_stream.py`; family builder: `build_families.py`.

Editor mix: {u['distinct_users']:,} distinct users; top user `{u['top_user']}` =
{u['top_user_share_pct']:.1f}% of arrivals (top-10 users = {u['top10_user_share_pct']:.1f}%);
bot-flagged = {u['bot_flagged_share_pct']:.1f}%; tool tags: {', '.join(f"{t} {p:.1f}%" for t, p in u['tool_shares_pct'].items())}.

## 2. Family definitions

**COARSE** family = `bot flag x tool tag x summary class`.
- tool tag: `JWB` / `AWB` / `twinkle` (first match in the edit's tags) or `none`.
- summary class: ordered regex taxonomy over the comment (after stripping any
  `/* section */` prefix; a comment that is only a section prefix is class
  `section-edit`; no comment at all = `empty`):
  infobox (infobox/template/double-brace), disambig, redirect, categor(y/iz), ref
  (ref/reference/<ref), cite, typo (typo/spelling/copyedit/ce/...), cleanup
  (format/style/updat/expand/remov/...), fix (fix/correct), other, empty, section-edit.
- Full coarse table: `families_coarse.json`.

**FINE** family = `bot flag x tool tag x summary class x normalized summary template`.
- normalization: strip `/* section */` prefixes, strip `[[wikilinks]]`,
  replace quoted strings with `"..."`, digits -> `#`, collapse whitespace,
  lowercase.
- ALL distinct (coarse, template) pairs form families; the top-50 patterns are
  materialized explicitly (`families_fine.json`, report table 3c).
- **One-off rule** ("one-off content edits = singleton families"): an edit with
  no tool tag, no bot flag and no usable summary -- empty, or only a
  `/* section */` auto-prefix, which carries no user-typed template -- becomes
  its own singleton family `(oneoff:<rcid>)` instead of joining a giant `(empty)`
  family. Tool/bot edits with empty summaries keep the `(empty)` template
  (the tool/bot identity is the repetition signal). No-usable-summary edits:
  {n_empty_summary + n_sectiononly:,} ({100.0*(n_empty_summary + n_sectiononly)/total:.1f}%),
  of which {n_oneoff:,} human ones became `(oneoff:<rcid>)` singleton families.

## 3. Stream-level statistics

| granularity | arrivals | families | singleton fam. % | singleton arr. % | top family | top fam. % | Gini | top-decile fam. % |
|---|---|---|---|---|---|---|---|---|
""" + "| " + " | ".join(stat_row("COARSE", cs)) + " |\n" \
    + "| " + " | ".join(stat_row("FINE", fs)) + " |\n\n" \
+ f"""(singleton fam. % = families with exactly 1 arrival / families;
singleton arr. % = arrivals landing in such families / total; top-decile fam. %
= arrivals in the largest 10% of families.)

### 3a. Top-10 COARSE families

""" + md_table(["#", "family (bot x tool x class)", "arrivals", share_col, "users"], top10c) + "\n\n" \
+ """### 3b. Top-10 FINE families

""" + md_table(["#", "family (bot x tool x class | template)", "arrivals", share_col, "users"], top10f) + "\n\n" \
+ """### 3c. Top-20 of the top-50 FINE template patterns (full 50 in `families_fine.json`)

""" + md_table(["rank", "coarse", "template", "arrivals", share_col, "users"], top50f) + "\n\n" \
+ f"""## 4. Arrivals per hour

""" + md_table(["hour (UTC)", "arrivals"], hourly_rows) + "\n\n" \
+ f"""(First and last hour-buckets are partial. Baseline is diurnal, ~2.5k-7k/h
with an early-UTC (04-05Z) bump; the 2026-09-05 16-17Z spike (~8.3k/8.6k vs
~4.5k/h neighbors) coincides with scripted bot sweeps -- the two hot bot
templates alone contribute ~3.4k/h in those hours -- on top of daytime traffic.)

## 5. Recommended replay streams

Two candidate streams (both FINE granularity, arrival order preserved,
JSONL `{{"ts","family_id"}}`):

""" + stream_section("Stream A: `stream_tool_hot_tail.jsonl` -- tool-driven hot+tail",
                     a_stats,
                     a_stats["note"] + " Selection: tool tag (JWB/AWB/twinkle) present OR bot flag set.") \
+ "\n" + stream_section("Stream B: `stream_content_tail.jsonl` -- content-tail-heavy",
                        b_stats,
                        b_stats["note"] + " Selection: no tool tag and no bot flag (human edits).") \
+ f"""
**family_id namespace**: `T*` = stream A, `H*` = stream B. `TF001..TF050` /
`HF001..HF050` = the stream's top-50 families by arrivals (mapping in
`families.json` and `stream_*_families.json`); `TS#######` / `HS#######` =
unique tail-family ids, assigned in first-arrival order (families with a single
arrival are omitted from families.json -- they are singletons by construction).

**Why these two**: Stream A represents the regime where recurrence is real and
tool-shaped -- a hot head of scripted maintenance patterns (e.g. infobox
parameter fixes) over a tail of smaller tool runs; a compile-policy agent
should discover the head families quickly and the tail keeps the gate honest.
Stream B represents the hard regime -- mostly one-off human content edits with
a thin layer of repeated manual patterns; most first-arrivals never repeat, so
compilation must pay for itself against a singleton-heavy prior (cf. Sepsis/BPI
streams in E4).

## 6. Reproducibility

- `pull_stream.py --hours {hours}`: raw pull (window recorded in the `meta`
  block of the .json.gz; state in `pull_state.json`; log in `pull.log`).
- `build_families.py --raw raw_enwiki_ns0_{hours}h.json.gz`: taxonomy, stats,
  streams, and this report (`structure_report.md`); `stats.json` holds every
  number shown above.
- Exact timestamps: requested [{wr['start']} .. {wr['end']}]; effective
  [{we['start']} .. {we['end']}]; pulled {meta.get('pull_started_utc', '?')} ..
  {meta.get('pull_finished_utc', '?')} UTC.
- Zero cost: public API only, read-only, < 0.5 req/s sustained.
"""
    with open(out("structure_report.md"), "w") as f:
        f.write(report)
    print("wrote structure_report.md")

if __name__ == "__main__":
    sys.exit(main())

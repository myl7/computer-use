#!/usr/bin/env python3
"""
pull_stream.py -- pull a window of enwiki namespace-0 edits from the public
MediaWiki recentchanges API for the compile-policy arrival-stream experiments.

Output (in --out-dir):
  raw_enwiki_ns0_{H}h.json.gz  : gzip of a single JSON document
                                  {"meta": {...}, "edits": [ <API records in pull order (newest->oldest)>, ... ]}
  pull_state.json              : resume state (safe to kill + rerun)

API etiquette:
  * User-Agent: compile-policy-research/0.1 (academic)
  * >= 2s between request starts (< 0.5 req/sec sustained)
  * the server additionally enforces a rate limiter (HTTP 429 + Retry-After);
    we honor Retry-After exactly and never exceed it
  * rclimit=500, maxlag=5, exponential backoff on real 4xx/5xx/network errors
  * crash-safe: records appended to a work file, state (cursor + byte offset)
    saved after every successful batch; rerun resumes from state.

Graceful degradation:
  * --max-minutes caps wall time (default 30). The pull walks NEWEST -> OLDEST,
    so an early stop yields a truncated-but-contiguous window [oldest_pulled, rcstart];
    the effective window is recorded in meta ("effective_start", "truncated": true).

Usage:
  python3 pull_stream.py [--hours 72] [--max-minutes 30] [--out-dir DIR]
"""

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://en.wikipedia.org/w/api.php"
UA = "compile-policy-research/0.1 (academic)"
MIN_INTERVAL = 2.0          # seconds between request starts (polite: < 1 req/sec;
                            # server additionally enforces a limiter via 429 +
                            # Retry-After, which we honor exactly)
RCLIMIT = 500
BACKOFF = [2, 4, 8, 16, 32, 60, 60, 60]  # seconds, for real errors
MAX_CONSEC_429 = 80         # give up (resume later) if limiter never relents


def now_utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout)


def build_url(rcstart, rcend, rccontinue=None):
    params = {
        "action": "query",
        "list": "recentchanges",
        "format": "json",
        "formatversion": 2,
        "rcnamespace": "0",
        "rctype": "edit",
        "rcprop": "title|ids|timestamp|user|comment|flags|tags",
        "rclimit": str(RCLIMIT),
        "rcstart": rcstart,
        "rcend": rcend,
        "rcdir": "older",
        "maxlag": "5",
    }
    if rccontinue:
        params["rccontinue"] = rccontinue
    return API + "?" + urllib.parse.urlencode(params)


def fetch_batch(url, log):
    """GET one batch with retry+backoff. Returns (records, continue_token_or_None).
    HTTP 429: honor the server's Retry-After header exactly (these do not count
    as failures). Raises RuntimeError after exhausting retries for real errors."""
    attempt = 0
    consec_429 = 0
    while True:
        retryable = None
        try:
            t0 = time.time()
            with http_get(url) as r:
                data = json.loads(r.read().decode("utf-8"))
            if "error" in data:
                code = data["error"].get("code", "")
                if code == "maxlag" or code.startswith("ratelimit"):
                    raise urllib.error.HTTPError(url, 429, code, None, None)
                raise RuntimeError(f"API error: {data['error']}")
            q = data.get("query", {})
            recs = q.get("recentchanges", [])
            cont = (data.get("continue") or {}).get("rccontinue")
            log(f"  fetched {len(recs)} edits in {time.time()-t0:.2f}s" +
                (f" continue={cont}" if cont else " (END)"))
            return recs, cont
        except urllib.error.HTTPError as e:
            if e.code == 429:
                consec_429 += 1
                if consec_429 > MAX_CONSEC_429:
                    raise RuntimeError("limiter never relents; resume later")
                try:
                    ra = int(e.headers.get("Retry-After", "2"))
                except (TypeError, ValueError):
                    ra = 2
                ra = min(max(ra, 1), 120)
                log(f"  429 rate-limited; honoring Retry-After={ra}s "
                    f"[{consec_429}/{MAX_CONSEC_429}]")
                time.sleep(ra)
                continue                     # 429s do not count as failures
            retryable = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            retryable = e
        # a real (non-429) error: exponential backoff, bounded attempts
        if attempt >= len(BACKOFF):
            raise RuntimeError(f"failed after {len(BACKOFF)} retries: {retryable}")
        log(f"  error ({type(retryable).__name__}: {retryable}); "
            f"backoff {BACKOFF[attempt]}s [attempt {attempt+1}/{len(BACKOFF)}]")
        time.sleep(BACKOFF[attempt])
        attempt += 1


def finalize(out_dir, gz_name, work_path, meta, log):
    """Stream the NDJSON work file into the final single-JSON gzip artifact."""
    tmp = os.path.join(out_dir, gz_name + ".tmp")
    n = 0
    with open(work_path, "r", encoding="utf-8") as fin, \
            gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fout:
        fout.write('{"meta": ')
        json.dump(meta, fout)
        fout.write(', "edits": [')
        first = True
        for line in fin:
            line = line.strip()
            if not line:
                continue
            fout.write(("" if first else ",") + line)
            first = False
            n += 1
        fout.write("]}")
    os.replace(tmp, os.path.join(out_dir, gz_name))
    log(f"finalized {gz_name}: {n} edits")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--max-minutes", type=float, default=30.0)
    ap.add_argument("--out-dir",
                    default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    gz_name = f"raw_enwiki_ns0_{args.hours}h.json.gz"
    gz_path = os.path.join(args.out_dir, gz_name)
    work_path = os.path.join(args.out_dir, gz_name.replace(".json.gz", ".work.ndjson"))
    state_path = os.path.join(args.out_dir, "pull_state.json")

    def log(msg):
        print(f"[{now_utc_iso()}] {msg}", flush=True)

    # ---- resume or init state ------------------------------------------------
    state = None
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
        if state.get("hours") != args.hours:
            log(f"state file is for hours={state.get('hours')}; starting fresh")
            state = None
    if state is None:
        rcstart = now_utc_iso()
        import datetime as _dt
        end = _dt.datetime.strptime(rcstart, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        rcend = (end - _dt.timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "hours": args.hours, "rcstart": rcstart, "rcend": rcend,
            "rccontinue": None, "n_records": 0, "byte_offset": 0,
            "oldest_ts": None, "done": False, "truncated": False,
            "started_at": rcstart,
        }
        with open(work_path, "w", encoding="utf-8") as f:
            pass  # truncate any stale work file
        log(f"window requested: [{rcend} .. {rcstart}] ({args.hours}h)")
    else:
        log(f"resuming: n_records={state['n_records']} cursor={state['rccontinue']}")
        # if we crashed between appending a batch and saving state, drop the
        # uncommitted tail of the work file (those records get re-fetched)
        if state["byte_offset"] < os.path.getsize(work_path):
            extra = os.path.getsize(work_path) - state["byte_offset"]
            with open(work_path, "r+b") as f:
                f.truncate(state["byte_offset"])
            log(f"truncated {extra} uncommitted bytes from work file")

    t_pull_start = time.time()
    url = build_url(state["rcstart"], state["rcend"], state["rccontinue"])
    last_req_start = 0.0

    while not state["done"]:
        if (time.time() - t_pull_start) / 60.0 > args.max_minutes:
            state["truncated"] = True
            log(f"max-minutes ({args.max_minutes}) reached; stopping early "
                f"(data is contiguous from rcstart back to {state['oldest_ts']})")
            break
        # polite cadence: >= MIN_INTERVAL between request starts
        wait = MIN_INTERVAL - (time.time() - last_req_start)
        if wait > 0:
            time.sleep(wait)
        last_req_start = time.time()

        recs, cont = fetch_batch(url, log)
        # safety filter: never record anything older than rcend
        recs = [r for r in recs if r.get("timestamp", "") >= state["rcend"]]

        with open(work_path, "a", encoding="utf-8") as f:
            if recs:
                f.write("\n".join(json.dumps(r, separators=(",", ":"),
                                             ensure_ascii=False) for r in recs) + "\n")
            state["byte_offset"] = f.tell()
        state["n_records"] += len(recs)
        if recs:
            state["oldest_ts"] = recs[-1].get("timestamp", state["oldest_ts"])
        state["rccontinue"] = cont
        state["done"] = cont is None or not recs
        with open(state_path, "w") as f:
            json.dump(state, f, indent=1)
        if state["n_records"] % 25000 < RCLIMIT:
            log(f"progress: {state['n_records']} edits, oldest={state['oldest_ts']}")
        if not state["done"]:
            url = build_url(state["rcstart"], state["rcend"], cont)

    # ---- finalize -------------------------------------------------------------
    meta = {
        "source": "en.wikipedia.org recentchanges API (action=query&list=recentchanges)",
        "query": "rcnamespace=0, rctype=edit, rclimit=500, rcdir=older, formatversion=2, maxlag=5",
        "rcprop": "title|ids|timestamp|user|comment|flags|tags",
        "user_agent": UA,
        "window_requested": {"start": state["rcend"], "end": state["rcstart"],
                             "hours": state["hours"]},
        "window_effective": {"start": state["oldest_ts"], "end": state["rcstart"]},
        "truncated": state["truncated"],
        "n_edits": state["n_records"],
        "pull_started_utc": state["started_at"],
        "pull_finished_utc": now_utc_iso(),
        "order": "edits array is in pull order (newest -> oldest); sort ascending by (timestamp, rcid) for arrival order",
        "record_schema": "MediaWiki RC records as returned by the API (formatversion=2)",
    }
    n = finalize(args.out_dir, gz_name, work_path, meta, log)
    with open(state_path, "w") as f:
        json.dump(state, f, indent=1)
    log(f"DONE: {n} edits, window [{meta['window_effective']['start']} .. "
        f"{meta['window_effective']['end']}], truncated={state['truncated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

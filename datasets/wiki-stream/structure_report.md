# Wikipedia maintenance editing: arrival-stream structure (T0.8)

Real arrival streams for the compile-policy experiments: each Wikipedia edit is
an arrival `(timestamp, family)`; family = tool tag x edit-summary template;
one-off content edits form singleton families.

## 1. Methodology

- **Source**: public MediaWiki API of `en.wikipedia.org`
  (`action=query&list=recentchanges`), `rcnamespace=0` (articles),
  `rctype=edit`, `rclimit=500`, `rcdir=older`, `rcprop=title|ids|timestamp|user|comment|flags|tags`,
  `formatversion=2`, `maxlag=5`. User-Agent `compile-policy-research/0.1 (academic)`.
- **Window requested**: 48h = [2026-09-03T18:48:26Z .. 2026-09-05T18:48:26Z] (rcstart = newer end, rcend = older end).
- **Window effective**: [2026-09-03T18:48:26Z .. 2026-09-05T18:48:26Z] (48h contiguous, truncation flag: False).
- **Fallback note**: a full 72h window was attempted first; the API's rate
  limiter (HTTP 429 + Retry-After) slowed pagination beyond the 30-minute
  budget, so the task's fallback to a 48h window was applied. The 48h window
  ends at the same `rcstart` as the aborted 72h attempt; the first ~6h of
  records were reused (the pull walks newest-to-oldest), the rest pulled with a
  2s request cadence honoring every `Retry-After`.
- **Rate policy**: >= 2s between request starts (< 0.5 req/s sustained), plus
  exact honoring of server `Retry-After`; crash-safe resume via cursor state.
  Pull effort: 385 successful list=recentchanges requests and 38
  honored rate-limit pauses for the final 48h run (plus a 70-request aborted
  72h attempt); wall time 2026-09-05T18:48:26Z ..
  2026-09-05T19:35:07Z UTC.
- **Cleaning**: dedupe by `rcid`, sort ascending by `(timestamp, rcid)` =
  arrival order. Total arrivals: **227,394 edits** over 49 hour-buckets
  (mean 4641/h, min 933 at 2026-09-03T18, max 8,552 at 2026-09-05T17; first/last buckets partial).
- **Raw pull**: `raw_enwiki_ns0_48h.json.gz` = gzip of one JSON document
  `{"meta": ..., "edits": [...]}`; edits are raw API records in pull order
  (newest -> oldest). Pull script: `pull_stream.py`; family builder: `build_families.py`.

Editor mix: 25,648 distinct users; top user `ZackBot` =
10.3% of arrivals (top-10 users = 26.2%);
bot-flagged = 23.0%; tool tags: none 83.6%, JWB 13.4%, AWB 2.0%, twinkle 1.1%.

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
  71,383 (31.4%),
  of which 71,361 human ones became `(oneoff:<rcid>)` singleton families.

## 3. Stream-level statistics

| granularity | arrivals | families | singleton fam. % | singleton arr. % | top family | top fam. % | Gini | top-decile fam. % |
|---|---|---|---|---|---|---|---|---|
| COARSE | 227,394 | 53 | 5.7% | 0.0% | 0|none|section-edit | 19.1% | 0.796 | 66.3% |
| FINE | 227,394 | 113,592 | 95.4% | 47.6% | 1|JWB|infobox|fixing deprecated parameters for []] ([]]) | 10.3% | 0.498 | 55.0% |

(singleton fam. % = families with exactly 1 arrival / families;
singleton arr. % = arrivals landing in such families / total; top-decile fam. %
= arrivals in the largest 10% of families.)

### 3a. Top-10 COARSE families

| # | family (bot x tool x class) | arrivals | share | users |
|---|---|---|---|---|
| 1 | bot=0 tool=none class=section-edit | 43,337 | 19.06% | 10414 |
| 2 | bot=0 tool=none class=other | 34,383 | 15.12% | 8197 |
| 3 | bot=0 tool=none class=empty | 28,024 | 12.32% | 7308 |
| 4 | bot=1 tool=JWB class=infobox | 23,543 | 10.35% | 1 |
| 5 | bot=0 tool=none class=cleanup | 21,428 | 9.42% | 6240 |
| 6 | bot=1 tool=none class=typo | 16,014 | 7.04% | 1 |
| 7 | bot=0 tool=none class=categor | 7,841 | 3.45% | 627 |
| 8 | bot=0 tool=none class=fix | 5,412 | 2.38% | 1679 |
| 9 | bot=0 tool=none class=infobox | 5,149 | 2.26% | 1068 |
| 10 | bot=0 tool=none class=typo | 4,942 | 2.17% | 2090 |

### 3b. Top-10 FINE families

| # | family (bot x tool x class | template) | arrivals | share | users |
|---|---|---|---|---|
| 1 | bot=1 tool=JWB class=infobox \| fixing deprecated parameters for []] ([]]) | 23,543 | 10.35% | 1 |
| 2 | bot=1 tool=none class=typo \| cleanup of deprecated parameter alma_mater ([]]) | 16,014 | 7.04% | 1 |
| 3 | bot=0 tool=JWB class=infobox \| fixing deprecated parameters for []] (manually doing those that could not be han | 3,506 | 1.54% | 1 |
| 4 | bot=1 tool=none class=infobox \| task#b: blacklist link removal. see []] | 2,874 | 1.26% | 1 |
| 5 | bot=0 tool=none class=categor \| added []] using []] | 2,824 | 1.24% | 259 |
| 6 | bot=0 tool=none class=cite \| using script-title parameter in citations for []] and []]. ([]] / []]) []] | 1,866 | 0.82% | 1 |
| 7 | bot=1 tool=none class=categor \| moving []] to []] per []] | 1,731 | 0.76% | 1 |
| 8 | bot=0 tool=AWB class=other \| []] | 1,672 | 0.74% | 2 |
| 9 | bot=1 tool=none class=fix \| []]: corrections to implement []] | 1,575 | 0.69% | 1 |
| 10 | bot=1 tool=none class=redirect \| bot: fixing double redirect from []] to []] | 1,358 | 0.60% | 2 |

### 3c. Top-20 of the top-50 FINE template patterns (full 50 in `families_fine.json`)

| rank | coarse | template | arrivals | share | users |
|---|---|---|---|---|---|
| 1 | bot=1 tool=JWB class=infobox | fixing deprecated parameters for []] ([]]) | 23,543 | 10.35% | 1 |
| 2 | bot=1 tool=none class=typo | cleanup of deprecated parameter alma_mater ([]]) | 16,014 | 7.04% | 1 |
| 3 | bot=0 tool=JWB class=infobox | fixing deprecated parameters for []] (manually doing those that could  | 3,506 | 1.54% | 1 |
| 4 | bot=1 tool=none class=infobox | task#b: blacklist link removal. see []] | 2,874 | 1.26% | 1 |
| 5 | bot=0 tool=none class=categor | added []] using []] | 2,824 | 1.24% | 259 |
| 6 | bot=0 tool=none class=cite | using script-title parameter in citations for []] and []]. ([]] / []]) | 1,866 | 0.82% | 1 |
| 7 | bot=1 tool=none class=categor | moving []] to []] per []] | 1,731 | 0.76% | 1 |
| 8 | bot=0 tool=AWB class=other | []] | 1,672 | 0.74% | 2 |
| 9 | bot=1 tool=none class=fix | []]: corrections to implement []] | 1,575 | 0.69% | 1 |
| 10 | bot=1 tool=none class=redirect | bot: fixing double redirect from []] to []] | 1,358 | 0.60% | 2 |
| 11 | bot=0 tool=none class=fix | standardizing full-width characters to ascii forms per []] and []], as | 1,344 | 0.59% | 1 |
| 12 | bot=0 tool=none class=other | ce | 1,092 | 0.48% | 271 |
| 13 | bot=0 tool=none class=other | undid revision []] by []] ([]]) | 1,090 | 0.48% | 586 |
| 14 | bot=0 tool=JWB class=fix | fix section link | 983 | 0.43% | 1 |
| 15 | bot=0 tool=none class=disambig | disambiguating links to []] (link changed to []]) using []]. | 936 | 0.41% | 10 |
| 16 | bot=0 tool=none class=categor | removed []]; added []] using []] | 931 | 0.41% | 103 |
| 17 | bot=1 tool=none class=other | rescuing # sources and tagging # as dead.) #iabot (v#.#.#.# | 909 | 0.40% | 1 |
| 18 | bot=0 tool=none class=other | []] | 884 | 0.39% | 226 |
| 19 | bot=0 tool=none class=infobox | replaced # bare urls by {{cite web}} | 814 | 0.36% | 1 |
| 20 | bot=1 tool=none class=cleanup | rescued # archive link; reformat # link. []] per []] passb | 786 | 0.35% | 1 |

## 4. Arrivals per hour

| hour (UTC) | arrivals |
|---|---|
| 2026-09-03 18 | 933 |
| 2026-09-03 19 | 4,284 |
| 2026-09-03 20 | 4,919 |
| 2026-09-03 21 | 5,998 |
| 2026-09-03 22 | 4,136 |
| 2026-09-03 23 | 4,765 |
| 2026-09-04 00 | 4,990 |
| 2026-09-04 01 | 5,263 |
| 2026-09-04 02 | 4,027 |
| 2026-09-04 03 | 5,603 |
| 2026-09-04 04 | 6,581 |
| 2026-09-04 05 | 7,139 |
| 2026-09-04 06 | 4,952 |
| 2026-09-04 07 | 4,720 |
| 2026-09-04 08 | 4,536 |
| 2026-09-04 09 | 4,589 |
| 2026-09-04 10 | 4,606 |
| 2026-09-04 11 | 4,888 |
| 2026-09-04 12 | 5,882 |
| 2026-09-04 13 | 6,165 |
| 2026-09-04 14 | 5,638 |
| 2026-09-04 15 | 5,235 |
| 2026-09-04 16 | 4,997 |
| 2026-09-04 17 | 4,362 |
| 2026-09-04 18 | 4,357 |
| 2026-09-04 19 | 4,788 |
| 2026-09-04 20 | 4,567 |
| 2026-09-04 21 | 4,390 |
| 2026-09-04 22 | 4,169 |
| 2026-09-04 23 | 3,956 |
| 2026-09-05 00 | 4,492 |
| 2026-09-05 01 | 3,663 |
| 2026-09-05 02 | 4,482 |
| 2026-09-05 03 | 4,585 |
| 2026-09-05 04 | 5,827 |
| 2026-09-05 05 | 3,666 |
| 2026-09-05 06 | 3,018 |
| 2026-09-05 07 | 2,713 |
| 2026-09-05 08 | 2,617 |
| 2026-09-05 09 | 2,836 |
| 2026-09-05 10 | 2,533 |
| 2026-09-05 11 | 3,176 |
| 2026-09-05 12 | 3,466 |
| 2026-09-05 13 | 3,862 |
| 2026-09-05 14 | 4,498 |
| 2026-09-05 15 | 4,680 |
| 2026-09-05 16 | 8,272 |
| 2026-09-05 17 | 8,552 |
| 2026-09-05 18 | 5,021 |

(First and last hour-buckets are partial. Baseline is diurnal, ~2.5k-7k/h
with an early-UTC (04-05Z) bump; the 2026-09-05 16-17Z spike (~8.3k/8.6k vs
~4.5k/h neighbors) coincides with scripted bot sweeps -- the two hot bot
templates alone contribute ~3.4k/h in those hours -- on top of daytime traffic.)

## 5. Recommended replay streams

Two candidate streams (both FINE granularity, arrival order preserved,
JSONL `{"ts","family_id"}`):

### Stream A: `stream_tool_hot_tail.jsonl` -- tool-driven hot+tail

tool-driven hot+tail: edits with tool tag (JWB/AWB/twinkle) or bot flag; family = fine (bot x tool x class x normalized template). Selection: tool tag (JWB/AWB/twinkle) present OR bot flag set.

- arrivals: **65,564**, families: **2,529** (singleton families: 81.34%; singleton arrival share: 3.14%)
- top family: **TF001** = 35.9% of arrivals; Gini = 0.955; top-decile-of-families share = 96.1%
- file: `stream_tool_hot_tail.jsonl` (`{"ts": "<ISO8601>", "family_id": "..."}` per line, arrival order ascending)

| family_id | label | arrivals | share | users |
|---|---|---|---|---|
| TF001 | bot=1 tool=JWB class=infobox \| fixing deprecated parameters for []] ([]]) | 23,543 | 35.91% | 1 |
| TF002 | bot=1 tool=none class=typo \| cleanup of deprecated parameter alma_mater ([]]) | 16,014 | 24.42% | 1 |
| TF003 | bot=0 tool=JWB class=infobox \| fixing deprecated parameters for []] (manually doing those that could not be han | 3,506 | 5.35% | 1 |
| TF004 | bot=1 tool=none class=infobox \| task#b: blacklist link removal. see []] | 2,874 | 4.38% | 1 |
| TF005 | bot=1 tool=none class=categor \| moving []] to []] per []] | 1,731 | 2.64% | 1 |

### Stream B: `stream_content_tail.jsonl` -- content-tail-heavy

content-tail-heavy: human edits (no tool tag, no bot flag); family = fine (bot x tool x class x normalized template); empty/section-only-summary edits are per-edit singleton families '(oneoff:<rcid>)'. Selection: no tool tag and no bot flag (human edits).

- arrivals: **161,830**, families: **111,063** (singleton families: 95.71%; singleton arrival share: 65.68%)
- top family: **HF001** = 1.8% of arrivals; Gini = 0.311; top-decile-of-families share = 38.2%
- file: `stream_content_tail.jsonl` (`{"ts": "<ISO8601>", "family_id": "..."}` per line, arrival order ascending)

| family_id | label | arrivals | share | users |
|---|---|---|---|---|
| HF001 | bot=0 tool=none class=categor \| added []] using []] | 2,824 | 1.75% | 259 |
| HF002 | bot=0 tool=none class=cite \| using script-title parameter in citations for []] and []]. ([]] / []]) []] | 1,866 | 1.15% | 1 |
| HF003 | bot=0 tool=none class=fix \| standardizing full-width characters to ascii forms per []] and []], as well as [ | 1,344 | 0.83% | 1 |
| HF004 | bot=0 tool=none class=other \| ce | 1,092 | 0.67% | 271 |
| HF005 | bot=0 tool=none class=other \| undid revision []] by []] ([]]) | 1,090 | 0.67% | 586 |

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

- `pull_stream.py --hours 48`: raw pull (window recorded in the `meta`
  block of the .json.gz; state in `pull_state.json`; log in `pull.log`).
- `build_families.py --raw raw_enwiki_ns0_48h.json.gz`: taxonomy, stats,
  streams, and this report (`structure_report.md`); `stats.json` holds every
  number shown above.
- Exact timestamps: requested [2026-09-03T18:48:26Z .. 2026-09-05T18:48:26Z]; effective
  [2026-09-03T18:48:26Z .. 2026-09-05T18:48:26Z]; pulled 2026-09-05T18:48:26Z ..
  2026-09-05T19:35:07Z UTC.
- Zero cost: public API only, read-only, < 0.5 req/s sustained.

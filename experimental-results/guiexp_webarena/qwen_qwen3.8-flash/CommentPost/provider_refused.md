# WebArena CommentPost x qwen/qwen3.8-flash — provider-refused (valid negative)

Date: 2026-09-20 (HKT), server cs11369a, lane `~/app/guiexp/webarena`, cap $1.00.

## Outcome

Closed as PROVIDER-REFUSED. The model's only OpenRouter endpoint (Alibaba
upstream) deterministically rejects the CommentPost task prompt with its input
content filter. No endpoint switch was made (operator ruling: model/routing
consistency beats coverage).

## Evidence

- Floor stage passed 18/18 no-task probe runs (generic page observations, no
  task text) — the refusal is content-filter-specific, not a general API or
  vision failure.
- Exploration seed 1, first agent call (the prompt carrying the CommentPost
  task goal) was rejected twice, identically — initial run and the single
  permitted restart (--resume reused floor/floor.json, then hit the same
  error at the same call). No task retries beyond that.

Verbatim error (both occurrences, different request ids):

```
openai.BadRequestError: Error code: 400 - {'error': {'message': 'Provider returned error',
'code': 400, 'metadata': {'raw': 'data: {"error":{"code":"data_inspection_failed","param":null,
"message":"Input text data may contain inappropriate content.","type":"data_inspection_failed"}...
', 'provider_name': 'Alibaba', 'is_byok': False}}
```

## Endpoints query (GET /api/v1/models/qwen/qwen3.8-flash/endpoints, 2026-09-20)

Exactly one endpoint, Alibaba-operated; no non-Alibaba route exists to consider
(independently confirmed by the operator, 2026-09-20: the other qwen ids on
OpenRouter are different models, out of scope):

```
name: Alibaba | qwen/qwen3.8-flash-20260826
provider_name: Alibaba
pricing: {"prompt": "0.00000015", "completion": "0.00000047",
          "input_cache_read": "0.000000016", "input_cache_write": "0.0000002", "discount": 0}
        (= $0.15/M in, $0.016/M cached-read, $0.47/M out, 1M ctx, status 200)
```

## Spend

$0.0130 (floor only; rejected calls are not billed). stages_done: ['floor'].

## Note on accounting constants

Launch required adding the model's price weights (live OpenRouter pricing
2026-09-20: input 1.5e-7, output 4.7e-7, cache-read 1.6e-8 USD/tok ->
r_c=0.107, r_o=3.13) to `guiexp_webarena/cost_ledger.py` PRICE_WEIGHTS (and
the same entry in the OSWorld lane's `guiexp_osworld/accounting.py`). No
experiment-design change.

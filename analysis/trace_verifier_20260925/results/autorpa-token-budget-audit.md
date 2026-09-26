# AutoRPA output-budget audit

Sources checked:

- AutoRPA paper v1, arXiv:2605.21082, especially Sections 3.4 and 4 and Appendices B, D, and F.
- Official repository `SusuOooo/AutoRPA`, commit `838933d9b6549687e4b9f8dbad283bed2ae244a3` (the repository's sole commit at audit time, dated 2026-06-29).

## Finding

The paper does not report a per-call output-token cap, `max_tokens`,
`max_completion_tokens`, context-window value, temperature, or reasoning-effort
setting. It reports aggregate consumed tokens and the structural budgets
`N=3`, `N_ref=2`, and `M=3`, but those are not output-token limits.

The official implementation's OpenAI path, used for GPT-4o, GPT-4.1, and
GPT-5, sets `temperature=0.0` and optionally passes `reasoning_effort`. It does
not pass `max_tokens` or `max_completion_tokens`. Therefore the repository does
not establish a finite AutoRPA output cap for the OpenAI experiments. The
effective cap is the provider/model default and is not reported by the paper or
pinned by the code.

The repository maps `gpt-5-low`, `gpt-5-medium`, and `gpt-5-high` suffixes to
the corresponding OpenAI `reasoning_effort`. At this commit, CLI defaults are
builder `gpt-5-medium`; default, analyzer, concluder, and summarizer
`gpt-5-low`; translator and parameter extractor `gpt-5-mini`; planner
`claude-sonnet-4-5`. These repository defaults postdate paper v1 and should not
be asserted as the exact paper experiment configuration unless a saved paper
command confirms them.

The Anthropic wrapper explicitly sends `max_tokens=8000` and
`temperature=0.0`. That is relevant to repository runs using
Claude Sonnet 4.5, but it is not the GPT-4.1/GPT-5 builder limit and does not
justify an 8k fairness cap for the paper's OpenAI results.

## Primary-source locations

- Paper: <https://arxiv.org/html/2605.21082v1>
- Official repository commit: <https://github.com/SusuOooo/AutoRPA/tree/838933d9b6549687e4b9f8dbad283bed2ae244a3>
- OpenAI wrapper omits any output-token cap: <https://github.com/SusuOooo/AutoRPA/blob/838933d9b6549687e4b9f8dbad283bed2ae244a3/androidworld/autorpa/utils/llm_client.py#L47-L153>
- Anthropic wrapper uses `max_tokens=8000`: <https://github.com/SusuOooo/AutoRPA/blob/838933d9b6549687e4b9f8dbad283bed2ae244a3/androidworld/autorpa/utils/llm_client.py#L159-L252>
- Model and reasoning mapping: <https://github.com/SusuOooo/AutoRPA/blob/838933d9b6549687e4b9f8dbad283bed2ae244a3/androidworld/autorpa/utils/llm_client.py#L371-L394>
- CLI model defaults: <https://github.com/SusuOooo/AutoRPA/blob/838933d9b6549687e4b9f8dbad283bed2ae244a3/androidworld/main.py#L121-L185>
- Builder uses the shared wrapper without a per-call token override: <https://github.com/SusuOooo/AutoRPA/blob/838933d9b6549687e4b9f8dbad283bed2ae244a3/androidworld/autorpa/core/rpa_builder.py#L88-L147>

## Fairness consequence

No exact OpenAI output cap can be matched from the published AutoRPA evidence.
A finite cap chosen for the current experiment must be described as this
experiment's generation setting, not as an AutoRPA-matched setting. A completed
model response that exhausts that active output budget is a model-generation
failure under the observed budget. It is distinct from provider unavailability,
transport failure, and runtime program-check failure. Availability retries do
not consume AutoRPA's `M=3` per-task refinement budget, while functioning
generation attempts remain visible and charged.

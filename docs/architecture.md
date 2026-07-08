# Architecture

<p align="center">
  <img src="assets/architecture.svg" alt="flightdeck architecture diagram" width="100%">
</p>

## The flow

1. **Declared intent** — an org is a directory of YAML under version control:
   `flightdeck.yaml` (identity, economics, policy), `models.yaml` (the governed registry),
   `usecases.yaml` (the backlog), `workflows/*.yaml` (promoted use cases with baselines and
   success criteria). Governance changes are pull requests.
2. **Governed execution** — `runner.execute()` runs the gates in order (budget → data policy
   → routing → redaction), calls the provider, and records the outcome. Every path —
   completed, blocked, failed — produces evidence.
3. **Evidence** — two artifacts under `.flightdeck/`: `runs.sqlite3` (the store: runs and
   human feedback) and `ledger.jsonl` (the hash-chained audit trail). Nothing else is state.
4. **Value** — `metrics.build_report()` computes KPIs from evidence with pure functions;
   `report/` renders the same OrgReport to the terminal and to a self-contained HTML
   dashboard. `backlog.py` scores what to do next.

## Design principles

- **Deterministic where trust matters.** Policy, routing, redaction, the ledger and every
  metric are pure code. The LLM only ever sits behind a provider adapter; it never
  participates in governance or measurement.
- **Fail closed, record the refusal.** No compliant model → blocked run, with the reason in
  the store and the ledger. Governance gaps surface as data, not as exceptions.
- **Evidence-only reporting.** If a number can't be recomputed from the store plus declared
  baselines, it doesn't appear on the dashboard.
- **Files over servers.** YAML in git, SQLite, JSONL, one HTML file. A pilot program should
  not start by operating a platform; everything here runs on a laptop and archives as plain
  files.
- **Conservative by default.** Unknown review time understates savings; unknown denominators
  render as "—"; partial weeks never chart.

## Module map

| Module | Responsibility | Depends on |
|---|---|---|
| `schemas.py` | typed domain model, strict validation | pydantic |
| `config.py` | org directory → validated aggregate, cross-checks | schemas |
| `policy.py` | data rules → cleared models; budget decisions; redaction default | config, store |
| `redact.py` | deterministic PII scrubbing | stdlib |
| `router.py` | tier routing among cleared models; fail-closed | schemas |
| `providers/` | `complete(spec, prompt, max_tokens)` — anthropic, openai/azure, mock | vendor SDKs (optional extras) |
| `runner.py` | the gate order; evidence on every path | all of the above |
| `store.py` | SQLite: runs + feedback | stdlib sqlite3 |
| `ledger.py` | hash-chained JSONL + verify | stdlib |
| `metrics.py` | evidence → KPIs (pure) | schemas |
| `backlog.py` | use-case scoring (pure) | schemas |
| `report/` | terminal + HTML rendering of an OrgReport | rich, jinja2 |
| `demo.py` | deterministic 13-week synthetic history | runner's record path |
| `cli.py` | the command surface | typer |

## Custom providers

The provider contract is one method — deliberately smaller than any vendor SDK:

```python
from flightdeck.providers import Completion
from flightdeck.schemas import ModelSpec

class MyGatewayProvider:
    def complete(self, spec: ModelSpec, prompt: str, max_output_tokens: int) -> Completion:
        response = my_client.generate(model=spec.model, prompt=prompt, base_url=spec.base_url)
        return Completion(text=response.text,
                          tokens_in=response.usage.input,
                          tokens_out=response.usage.output)
```

Register the instance by passing it to `runner.execute(provider=...)`, or add a branch in
`providers.get_provider()` in a fork. This is also the seam for teams whose "workflow" is a
PydanticAI/LangGraph agent: wrap the agent invocation in an adapter and its runs join the
same store, ledger and reports — flightdeck doesn't care how the text was made, only what it
cost, what data class it carried and what a human did with it.

Real token usage must come from the vendor response, never estimated — the numbers downstream
inherit whatever honesty the adapter has.

## Data locations

```
<org>/
  flightdeck.yaml   models.yaml   usecases.yaml   workflows/*.yaml    # intent (commit these)
  .flightdeck/
    runs.sqlite3    # evidence: runs + feedback  (never commit)
    ledger.jsonl    # audit chain               (never commit; archive to WORM storage)
  dashboard.html    # generated artifact
```

## Non-goals

Orchestration (agent frameworks own it), traffic proxying (gateways own it; compose via
`base_url`), semantic DLP, multi-writer servers, BI. The README's
["What flightdeck is not"](../README.md#what-flightdeck-is-not) is normative.

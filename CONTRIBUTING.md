# Contributing

Small, deterministic, honest — contributions should keep all three.

## Setup

```bash
git clone https://github.com/sturlese/flightdeck.git && cd flightdeck
python3 -m venv .venv && source .venv/bin/activate
make install        # pip install -e ".[dev]"
make test lint      # pytest (85% coverage gate) + ruff
flightdeck demo     # the end-to-end sanity check, offline
```

## Ground rules

- **The deterministic core stays deterministic.** Policy, routing, redaction, the ledger and
  every metric are pure code; no LLM participates in governance or measurement. PRs that
  blur that line will be asked to un-blur it.
- **Every metric change updates docs/metrics.md and its tests in the same PR.** The formula
  file is a contract; code and contract never drift.
- **Conservative bias is normative** (ADR 004): if a change can overstate or understate
  savings under uncertainty, it must understate.
- **The demo must stay offline and deterministic** — it is CI's end-to-end test and the
  first thing every evaluator runs.
- New governance events, policy axes or providers: include the ADR-level "why" in the PR
  description; significant direction changes add a file under `docs/decisions/`.

## Style

Ruff (config in `ruff.toml`), type hints throughout, module docstrings that explain design
intent rather than restating code. Tests live in `tests/` and run offline.

# ADR 001 — Measure business outcomes, not LLM calls

## Context

The obvious way to "measure an AI program" is telemetry: traces, tokens, latency, cost per
call. Excellent tools exist for exactly that (Langfuse, Helicone, vendor consoles), and the
first design question was whether flightdeck should be one more of them, perhaps with nicer
charts.

But telemetry answers *what did the model do*, and the people funding a transformation ask a
different question: *what did we get* — hours back, processes automated, money saved, people
actually using it. No amount of token data answers that, because the answer requires three
things no proxy or trace can see: the **human baseline** the AI replaces, the **human
verdict** on each output, and an **adoption denominator** (who could be using this vs. who
is). Meanwhile the "definition of success" for every AI-transformation owner we looked at is
written in exactly those terms.

## Decision

flightdeck's unit of measurement is the **workflow with a declared baseline**, not the LLM
call. Every workflow must state the human minutes-per-task and monthly volume it replaces;
every run may receive a human verdict (accepted / edited / rejected + minutes spent); every
KPI is computed from those two facts plus recorded cost. Token-level observability is
explicitly delegated to the tools that already do it well — the layers meet at the provider
call and coexist.

Consequently the schema *forces* the conversation most pilots skip: you cannot register a
workflow without a baseline, and you cannot claim ROI without either reviews or a conscious
`review: none` declaration.

## Consequences

- The dashboard speaks the language of the people who decide budgets; nothing on it needs a
  token explained.
- Baselines are self-declared and can drift — mitigated by making them versioned, visible
  and cheap to recalibrate (docs/metrics.md prescribes quarterly re-timing).
- flightdeck will never be a debugging tool for prompts; that is a feature, not a gap.
- Some value (quality lift, speed-to-answer, morale) is deliberately not counted — see
  ADR 004 for why the bias is always toward understatement.

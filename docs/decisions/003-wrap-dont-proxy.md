# ADR 003 — Wrap the call in-process; don't build a gateway

## Context

Policy enforcement for LLM traffic usually means a gateway: a proxy (LiteLLM, Portkey, Kong
AI) that all requests traverse, enforcing quotas and allowlists at the network edge.
Gateways are good infrastructure, and the temptation to build "a small one, with governance"
was real.

But the rules flightdeck enforces are **business-context rules**: *this workflow carries
confidential data*, *this task's baseline justifies a frontier model*, *this month's budget
for this workflow is spent*. A proxy sees none of that — it sees an HTTP request with a
model name. Pushing workflow identity, data classification and baselines into headers so an
edge component can reason about them is how a small proxy becomes a distributed system.

## Decision

Policy runs **in-process, before the payload leaves**: the runner resolves the workflow's
classification against the registry, checks the budget against the store, redacts variables,
and only then constructs the provider call. flightdeck wraps; it never proxies. Where a
gateway already exists, it composes underneath through the registry's `base_url` — the
gateway keeps doing network-edge enforcement (rate limits, key management), flightdeck keeps
doing business-context governance, and neither needs to know much about the other.

## Consequences

- Zero standing infrastructure: governance works on a laptop, in CI, in a cron job.
- Redaction happens before egress *from the process* — the strongest place to make the
  "PII never left" claim without TLS interception.
- The trade: flightdeck only governs traffic that goes through flightdeck. A developer
  calling a vendor SDK directly bypasses it — that is an acceptable-use and platform-config
  problem, stated honestly in docs/governance.md, not silently claimed as covered.
- No cross-host quota aggregation; budgets are per org directory. Fine at pilot scale,
  revisit with a shared deployment (same trigger as ADR 002's server).

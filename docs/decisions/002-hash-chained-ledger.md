# ADR 002 — A hash-chained JSONL file as the audit ledger

## Context

Governance oversight in a regulated environment needs an audit trail that a non-engineer
(auditor, DPO, board member) can be told about in one sentence and that a program can verify
in milliseconds. The candidates: rows in the SQLite store, an external audit service, a
proper immutable log system (Kafka + WORM storage, QLDB-style ledgers), or a flat file.

Rows in SQLite are silently editable — an audit trail whose integrity depends on nobody
having opened the database is not one. External services and log infrastructure move the
trust problem somewhere real but cost an operational dependency that a pilot-stage AI
program will simply not deploy — and an audit control that doesn't get deployed protects
nothing.

## Decision

An **append-only JSONL file where every entry embeds the SHA-256 of the previous entry**.
`flightdeck audit verify` re-walks the chain in pure code: any edited, deleted or reordered
line breaks every hash after it. Run outputs are not stored in the ledger; their SHA-256 is,
so content tampering in the store is detectable without the audit file accumulating
sensitive text. One writer per org directory, by design; timestamps may be supplied for
imports and the demo seeder, because the *sequence* proves append order regardless.

## Consequences

- The whole mechanism is ~100 lines, readable by an auditor's technical advisor in one
  sitting, and works on a laptop — so it actually ships on day one.
- It is tamper-**evident**, not tamper-**proof**: an attacker with write access can rewrite
  the file and recompute the chain. The documented mitigation is periodic shipping to WORM
  storage (object lock), which converts "rewrite history" into "rewrite history before the
  last archive, detectably".
- Single-writer is a real constraint: concurrent CLI invocations on the same org are not
  supported. Accepting it deferred an entire server from v0; revisit only when a shared
  deployment is a proven need.
- JSONL means `less`, `grep` and `diff` are the audit tooling — deliberately boring.

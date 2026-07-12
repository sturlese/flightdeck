# Governance — what is enforced, what is recorded, what is out of scope

flightdeck's governance stance: **decide in pure code before the payload leaves, record
everything, fail closed, and keep the rules in version control** where they have diffs,
authors and reviewers. This file explains the mechanisms and — just as important — their
boundaries.

## Data classifications

Every workflow declares one of four classes; every class maps to a rule in
`flightdeck.yaml → policy.data_rules`:

| Class | Default rule |
|---|---|
| `public` | any registered model |
| `internal` | vendors must not train on the data |
| `confidential` | no training vendors (add regions/providers per your posture) |
| `restricted` | no training vendors **and an explicit model allowlist — empty by default, so it fails closed** until the org decides |

A rule constrains model attributes from the registry: `regions`, `providers`, explicit
`models`, `forbid_training_vendors`. The registry rows (`models.yaml`) carry those facts —
`region`, `trains_on_data`, prices — and **you are asserting them**: verify residency and
training use against your vendor agreement (DPA) before relying on the rule.

Partial overrides are safe by construction: an org file that only redefines `restricted`
keeps the conservative defaults for every other class.

## The gates, in order

`flightdeck run` decides everything before any network call:

1. **Budget gate** — monthly cap (workflow's, else policy default) vs. committed spend in the
   store. Exhausted → the run is `blocked`, visibly. An over-budget pilot is a governance
   signal, not an outage.
2. **Data policy → router** — the classification's rule filters the registry; the router picks
   the cheapest cleared model in the declared tier, escalates *upward* if the tier is empty,
   and **fails closed** (blocked run, reason recorded) if nothing is cleared. Quality is never
   silently downgraded and policy is never "just this once" bypassed.
3. **PII redaction** — templated variables (the org data: tickets, contracts, notes) are
   scrubbed in-process; prompt scaffolding (authored by the org) is not. Every hit is counted
   on the run and totalled on the dashboard.

Blocked and failed runs land in the store *and* the ledger with their reason — the program
learns as much from its refusals as from its successes.

## Scheduled runs (`schedule:` + `flightdeck tick`)

A review-free workflow (`review: none`) may declare a `schedule:` block — a `cadence`
(`daily` / `weekly` / `monthly`) and the `vars` its steps need (there is no human to pass
`--var` to a digest bot). A schedule on a human-reviewed workflow is a **loud config error**:
scheduling means running unattended, and silently dropping the review is exactly the kind of
governance typo strict schemas exist to reject.

flightdeck does **not** reimplement cron. An external scheduler (cron, a CI job) invokes
`flightdeck tick` as often as it likes; `tick` runs each due workflow **at most once per
cadence period**. Due-ness is a *calendar period*, not a rolling window: a daily workflow is
due unless some run already started today, weekly unless one started this ISO week, monthly
unless one started this month. Crucially, **any** run in the period counts — completed,
blocked *or* failed — so a budget-blocked attempt still spends the period. That is what makes
the week-9 runaway-bot scenario impossible by construction: even 300 `tick` calls in an hour
run a daily digest exactly once that day, and every later call sees the period spent and skips
it. Scheduled runs are attributed to the `scheduler` service account, pass through the same
gates, and land in the store and ledger as `run_completed` / `run_blocked` / `run_failed` like
any other run. `tick` is a batch: it exits 0 even when some runs block (an expected governance
signal), reserving non-zero for usage/config errors — cron alerts on the ledger, not the
exit code.

## Redaction: a seatbelt, not a DLP suite

Deterministic regex patterns (emails, phones with E.164 digit-count checks, IBANs,
Luhn-validated card numbers, API-key shapes, Spanish DNI) plus org-specific patterns
declared in `flightdeck.yaml → policy.redact_patterns` (a list of regexes, validated at
load; hits are counted on the run like the built-ins'). Design
bias: **precision over recall** — a redactor that mangles half the prompt gets switched off by
annoyed users, which protects nobody. It will not catch free-text PII ("my neighbour Marta from
the 3rd floor"), and it does not try. If your data demands semantic PII detection, put a
dedicated system in front and keep flightdeck's counter as the audit signal.

## The audit ledger

Append-only JSONL; each entry carries the SHA-256 of the previous one:

```json
{"seq": 1042, "at": "…", "event": "run_completed",
 "data": {"run_id": "…", "workflow": "…", "cost": 0.031, "output_sha256": "…"},
 "prev": "…", "hash": "…"}
```

- **Tamper-evident, not tamper-proof:** editing or deleting any line breaks every hash after
  it — `flightdeck audit verify` re-walks the chain in pure code and exits non-zero on the
  first break. It cannot stop a root user from rewriting the whole file *and* recomputing the
  chain; for that, ship the file to append-only storage (S3 object lock, a log pipeline) on
  your schedule. ([ADR 002](decisions/002-hash-chained-ledger.md))
- **Content is sealed, not stored:** outputs live in the store; the ledger keeps their SHA-256,
  so an edited output is detectable without the audit trail accumulating sensitive text.
- **Single-writer by design.** One org directory, one ledger. Concurrent writers need a
  server, and a server is out of scope for v0.

Events: `run_completed`, `run_blocked`, `run_failed`, `feedback_recorded`, `demo_seeded`.

## Capturing feedback where reviewers are (Slack)

Review coverage is the bottleneck on the ROI evidence: if reviewers don't record what they did
with an output, the hours-saved number stays a guess. Reviewers live in Slack, not a terminal,
so flightdeck can post a run to a channel with **Accept / Edited / Reject** buttons and turn a
click back into the same measurement `flightdeck feedback` records.

- **One feedback path.** The CLI command and the Slack handler both call a single
  `record_feedback(...)` function, so a button click lands the *identical* store row and the
  *identical* `feedback_recorded` ledger event (`{run_id, outcome, human_minutes, by}`) — the
  Slack `by` is the reviewer's Slack handle, with a `via slack` note on the store row for
  provenance. There is no second, weaker feedback API to keep in sync.
- **Offline-first, no new dependency.** `flightdeck slack post <run_id>` renders a Slack Block
  Kit message and, by default, **prints the JSON** — fully demoable and pipeable to any poster.
  Only when `FLIGHTDECK_SLACK_WEBHOOK` is set does it actually POST, via stdlib `urllib`
  (the transport is injectable; the core never imports networking). `flightdeck slack handle`
  reads an interaction payload on stdin, so a tiny serverless function — or `curl | flightdeck
  slack handle` — closes the loop.
- **Minutes are optional.** Buttons can't collect free text, so a plain click records no
  minutes and the metrics fall back to the org's conservative `default_review_minutes`. An
  optional modal collects an explicit figure when a reviewer wants to be precise.

## What the dashboard's governance panel asserts

- policy blocks and budget blocks (window and all-time — old incidents stay visible),
- failed runs, PII redactions before egress,
- **model residency mix** and **share of runs on non-training vendors** — computed from the
  registry facts of the models actually used,
- ledger integrity (verified at report time, every time).

## User attribution and the SSO directory

Runs attribute to a user. Left as free text, `ana`, `Ana G.` and
`ana.garcia@example.com` are three different people to the KPIs, and the adoption
denominator (eligible vs. active users per department) comes from hand-maintained
YAML headcounts rather than the source of truth. An optional **synced directory
snapshot** fixes both, without adding any network dependency to the core.

- **The snapshot — `directory.yaml` at the org root** (optional; absent changes
  nothing). Top-level `{provider, users}`, where `provider` is metadata
  (`file` | `azure_ad` | `google_workspace`) recording where the snapshot came
  from, and each user carries a **stable `id`** (an Azure objectId, a Google id,
  an employee number — *not* a slug), a `display_name`, and optional `email`,
  `department`, `active` (default `true`) and `aliases` (old usernames /
  sam-account-names that resolve to the same person):

  ```yaml
  provider: azure_ad
  users:
    - id: AAD-8f3c…            # the stable id — this is what gets stored on a run
      display_name: Ana García
      email: ana.garcia@example.com
      department: Support
      active: true
      aliases: [ana, agarcia]
  ```

  Loading is strict, like every other governance file: an unknown key or a
  duplicate `id` fails loudly with `directory.yaml` named.

- **Resolution is deterministic.** At run time the effective user (`--user`, else
  the OS user) is resolved through the directory with a fixed precedence: exact
  **id**, then **email**, then **alias**, then **display_name** (every match after
  id is case-insensitive). First match wins; nothing matches → the raw string is
  kept exactly as before, so an unknown user is never lost.

- **Privacy: store ids, render names.** A resolved run stores the **stable id**
  (an opaque key), never the free-text handle; display names are rendered only at
  report time and fall back to the id for anyone not (yet) in the directory. The
  reports today show user *counts* and adoption — computed over distinct stable
  ids — so nothing individual is surfaced; the display-name resolver exists for
  any surface that later needs it.

- **Directory-sourced denominators.** When the directory has active members in a
  department, that resolved count becomes the adoption denominator
  (`Org.department_headcount` → `eligible_users`), overriding the YAML headcount;
  absent members fall back to the declared headcount. An explicit
  `workflow.eligible_users` still wins over both. Inactive members never count.

- **The sync is read-only and out-of-band.** The offline core only ever *reads*
  `directory.yaml`. Refreshing it from a live provider is a documented, pluggable
  adapter (`Directory.from_provider`), mirroring the provider adapters: for
  `azure_ad` / `google_workspace` it raises a clear "needs the extra +
  credentials" error until wired, so the core and the test suite never touch a
  network. A real sync runs on a schedule (CI/cron), installs the extra
  (`ai-flightdeck[azure]` → Microsoft Graph `/users`; `ai-flightdeck[google]` →
  Admin SDK Directory `users.list`), maps each record to a user (stable id →
  `id`), writes the snapshot, and commits it — so the directory itself changes by
  reviewed diff, like the rest of the governed files.

## Operating guidance

- The policy block and the model registry are **owned artifacts** (typically the AI lead +
  DPO/legal). Change them by pull request; the diff is the approval record.
- Schedule `flightdeck audit verify` (cron/CI) and alert on non-zero exit.
- Review `restricted`/`confidential` allowlists quarterly and whenever a vendor contract
  changes.
- Treat blocked-run spikes as signals: a policy gap (nobody can run legally) or a rogue
  automation (the cap is doing its job).

## Explicitly out of scope

Naming the boundary is part of the governance:

- Traffic that doesn't go through flightdeck — someone pasting a contract into a public
  chatbot is an acceptable-use policy problem; flightdeck reduces the *reasons* to do it.
- Prompt injection and model-output safety — review modes and acceptance tracking are the
  mitigation surface here, not a filter.
- Vendor-side logging/retention beyond what your DPA says — the registry records your
  assertion; it cannot audit the vendor.
- Legal compliance conclusions. flightdeck produces the *evidence trail* (who ran what, where,
  under which rule); your counsel maps it to EU AI Act / GDPR / internal frameworks.

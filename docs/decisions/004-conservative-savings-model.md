# ADR 004 — A savings model that prefers understating to arguing

## Context

"Hours saved" is the number this whole system exists to produce, and it is also the easiest
number to inflate — which is why so many AI-ROI slides collapse under the first skeptical
question. The failure modes are known: counting every generated output as a full task done,
surveying "perceived productivity", ignoring the time humans spend fixing mediocre outputs,
and letting automation volume (or a runaway loop) multiply phantom hours.

The measurement had to survive an audience whose default is disbelief: a CFO, an auditor, a
board. Against that audience, a number's defensibility is worth more than its size.

## Decision

Four rules, enforced in `metrics.py` and pinned by tests:

1. **Unmeasured is not saved.** Human-in-the-loop runs with no review earn zero. Coverage
   gaps shrink the claim instead of inflating it.
2. **Rejected outputs earn negative minutes.** A human spent review time and got nothing;
   the program pays for it in the headline number, visibly.
3. **Caps.** No run earns more than its own baseline, and no workflow earns more per
   calendar month than `minutes_per_task × tasks_per_month` — the volume it *declared*.
   Duplicate runs and retry storms hit the cap and credit nothing further (the week-9
   incident in the demo exists to demonstrate exactly this).
4. **Time is the only claimed benefit.** Quality, speed, morale and revenue effects belong
   in the narrative with their own evidence, never smuggled into an hours figure.

Where a default must exist (review time nobody recorded), it is a visible org-level constant
(`default_review_minutes`), not a hidden heuristic.

## Consequences

- The reported number is defensible line-by-line: every hour traces to a run, a verdict and
  a declared baseline. "Says who?" has an answer.
- The number is smaller than what enthusiasts will feel is true. That is the point; program
  owners can present upside as narrative on top of a floor that nobody can puncture.
- `review: none` remains an honesty valve that trusts a declaration — the docs flag it, the
  health column shows `no_target`/no reviews, and `spot_check` exists as the middle path.
- Teams that never record minutes get coarser (more conservative) numbers — an incentive
  aligned with the behavior the program wants anyway.

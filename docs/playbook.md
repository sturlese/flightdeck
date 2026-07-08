# The 90-day playbook — running an AI transformation on evidence

flightdeck is a tool; this is the method it was built to serve. It assumes one accountable
owner (an AI lead / transformation manager), an executive sponsor, and an organization where
people are busy and skeptical — which is to say, every organization.

The through-line: **declare before you pilot, measure while you run, decide on the record.**

## Weeks 0–2 · Baseline and backlog

**Goal: a scored backlog and a signed policy, before any prompt is written.**

1. Interview each function's lead and their busiest operator (30 min each). You are hunting
   for tasks that are *frequent, repeatable, text-heavy and annoying* — not for "AI ideas".
   For each candidate capture the facts `usecases.yaml` needs: minutes per task, volume per
   month, who does it, how risky, how clean the inputs.
2. Time the manual task where anyone hesitates. A stopwatch beats a guess; the baseline is
   the foundation of every number you will ever report.
3. Write `usecases.yaml`. Run `flightdeck backlog`. Argue about the *inputs*, not the
   ranking — that argument is the alignment.
4. Close the governance file with legal/DPO **now**, while nothing is on fire: data
   classifications, the model registry with residency and training-use facts from your
   vendor agreements, budget caps. `restricted` starts with an empty allowlist on purpose.
5. Set the operating cadence: weekly 30-min review with pilot owners, monthly steering with
   the sponsor. Put both in calendars before the first pilot.

Exit criteria: backlog scored and argued; policy merged; steering booked.

## Weeks 3–6 · Pilots that can fail

**Goal: 2–3 pilots live, generating evidence — not applause.**

1. `flightdeck promote` the top 2–3. Small beats ambitious: a support-reply drafter that runs
   600 times a month teaches you more than a moonshot that runs twice.
2. Write success criteria INTO the workflow file before launch (acceptance target, weekly
   active users). Declared targets are what make week-10 decisions boring — the goalpost has
   an author and a diff.
3. Every pilot starts `human_in_the_loop`. No exceptions in the first 90 days; `review: none`
   is something a workflow *earns* with a track record.
4. Train the pilot group (30 minutes, hands-on): run the workflow on their real work, record
   feedback with `--minutes`, and explain *why the minutes matter* — their feedback is the
   ROI evidence, and they should see the dashboard their verdicts build.
5. Review `flightdeck report` weekly with the owners. Look at acceptance and rejection
   *reasons* first, adoption second, hours third. Iterate prompts in small diffs.

Exit criteria: every pilot has ≥ 3 weeks of runs, review coverage above ~80%, and a first
prompt iteration behind it.

## Weeks 7–10 · Scorecards and the first kill

**Goal: scale/kill decisions made against declared criteria, on the record.**

1. Bring the dashboard to steering (`flightdeck report --html board.html`). Walk the health
   column, not the screenshots of chat outputs.
2. **Scale** what is `healthy`: widen the user group, raise volume, consider `spot_check`
   for workflows with consistently high acceptance.
3. **Rework or kill** what is `underperforming`. One honest kill, announced with its numbers,
   buys more credibility than three glossy launches — and the ledger shows the pilot was
   given its fair run.
4. Promote the next backlog wave with the capacity you freed.
5. Start the enablement artifacts where demand already exists: a prompt library from the
   workflows that won, a one-page "how to review AI output" guide, office hours.

Exit criteria: at least one scale decision and one rework/kill decision, both traceable to
the declared criteria.

## Weeks 11–13 · Make it boring

**Goal: the program runs as infrastructure, not as a project.**

1. Monthly dashboard to the board; same format every month, trends over snapshots.
2. Tune budget caps to observed spend (generous enough to never block honest work, tight
   enough to catch a runaway loop — the demo's week-9 incident is the sizing lesson).
3. `flightdeck audit verify` in cron/CI with an alert on failure; archive the ledger to
   append-only storage on your retention schedule.
4. Recalibrate baselines quarterly (re-time the manual task) and prune the registry against
   procurement reality.
5. Write down the operating model — who owns the policy file, who approves new workflows,
   how a department requests one — so the program survives your vacation.

## Anti-patterns (all field-tested, all expensive)

- **Pilot theater.** Ten demos, zero baselines, nothing measured. If it has no baseline, it
  is a demo, not a pilot.
- **Tool-first rollout.** Buying licenses and hoping use cases appear. The backlog comes
  first; tools serve it.
- **Survey ROI.** "Do you feel more productive?" is not a number a CFO can audit. Minutes
  are.
- **Goalpost surgery.** Changing success criteria after seeing the data. Declared targets in
  version control make this visible — that is their real job.
- **Unreviewed automation creep.** Quietly flipping pilots to `review: none` to juice the
  hours number. The metrics doc calls this out for a reason; the discomfort is the control.
- **Governance as a launch blocker.** Policy written in week 12 blocks everything forever.
  Written in week 1, it is two YAML files and nobody notices the seatbelt until it holds.

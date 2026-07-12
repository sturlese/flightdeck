"""Starter files for ``flightdeck init`` — a working org in five minutes.

The starter is generic and heavily commented: it exists to be edited. It ships
with the offline mock provider wired in so the first run works before any API
key or vendor conversation, plus commented real-provider entries to promote.
"""

ORG = """\
# Your organization. Everything in this file is reviewable governance —
# keep it in version control and change it by pull request.
name: My Company
currency: EUR
default_hourly_cost: 45.0     # used when a use case / workflow doesn't set its own
default_review_minutes: 2.0   # assumed review time when feedback doesn't record minutes

departments:
  - name: Operations
    headcount: 10
  - name: Finance
    headcount: 5

policy:
  redact_pii_default: true
  # Org-specific redactions, applied on top of the built-in PII patterns
  # (emails, phones, IBANs, cards, api keys). Quote regexes in single quotes.
  # redact_patterns:
  #   - '\\bEMP-\\d{5}\\b'        # e.g. employee ids
  data_rules:
    # Defaults are conservative: anything beyond `public` never reaches a vendor
    # that trains on your data, and `restricted` fails closed until you list
    # explicitly approved models. Loosen deliberately, in a reviewed diff.
    restricted:
      forbid_training_vendors: true
      models: []   # e.g. [sonnet-eu] once approved
"""

MODELS = """\
# The governed model registry. Prices are EXAMPLES in your org currency —
# replace them with your negotiated rates. `region` and `trains_on_data` are
# the governance facts the policy engine reasons over: verify both against
# your vendor agreement (DPA) before relying on them.
models:
  - id: mock-fast
    provider: mock
    model: mock-small
    tier: fast
    input_cost_per_mtok: 0.5
    output_cost_per_mtok: 2.0
    region: local
    trains_on_data: false
    notes: Offline provider — works with no API key; replace once real providers are wired.

  - id: haiku
    provider: anthropic
    model: claude-haiku-4-5
    tier: fast
    input_cost_per_mtok: 1.0
    output_cost_per_mtok: 5.0
    region: global          # set to your deployment's residency (e.g. eu via Bedrock/Vertex)
    trains_on_data: false   # API default — confirm in your DPA
    notes: Needs ANTHROPIC_API_KEY and `pip install 'ai-flightdeck[anthropic]'`.

  - id: sonnet
    provider: anthropic
    model: claude-sonnet-5
    tier: balanced
    input_cost_per_mtok: 3.0
    output_cost_per_mtok: 15.0
    region: global
    trains_on_data: false

  - id: gpt-mini
    provider: openai
    model: gpt-5.4-mini
    tier: fast
    input_cost_per_mtok: 0.6
    output_cost_per_mtok: 2.4
    region: global          # for Azure OpenAI set base_url and your region
    trains_on_data: false
    notes: Needs OPENAI_API_KEY and `pip install 'ai-flightdeck[openai]'`.
"""

USECASES = """\
# The backlog: candidates for automation with the facts that make them
# scoreable. `flightdeck backlog` ranks these; `flightdeck promote <id>`
# scaffolds a workflow from one.
usecases:
  - id: meeting-minutes
    name: Meeting minutes drafting
    department: Operations
    description: Turn raw meeting notes into structured minutes with actions.
    task_minutes: 20          # human minutes per task today
    tasks_per_month: 40
    automation_potential: 0.7 # share of the task AI can realistically take
    data_readiness: 4         # 1–5: are the inputs clean and reachable?
    process_stability: 4      # 1–5: same steps every time?
    risk: 2                   # 1–5: data sensitivity / error blast radius
    effort_weeks: 1
    status: candidate         # candidate | piloting | live | killed
"""

WORKFLOW = """\
# A promoted use case: executable steps plus the three things pilots forget
# to write down — the baseline to beat, the data classification that gates
# where it may run, and the success criteria that decide scale-or-kill.
id: meeting-minutes
name: Meeting minutes drafting
department: Operations
use_case: meeting-minutes
data_classification: internal
tier: fast                       # fast | balanced | frontier — never a model name
review: human_in_the_loop        # human_in_the_loop | spot_check | none

baseline:
  minutes_per_task: 20
  tasks_per_month: 40

steps:
  - id: draft
    vars: [notes]
    max_output_tokens: 800
    prompt: |
      Turn these raw meeting notes into minutes with three sections:
      Decisions, Action items (owner + due date if stated), Open questions.
      Do not invent owners or dates that are not in the notes.

      NOTES:
      {{notes}}

guardrails:
  redact_pii: true
  monthly_budget: 25   # in the org currency; blocks (visibly) when exhausted

success:
  weekly_active_users_target: 4
  acceptance_target: 0.8
"""

GITIGNORE = """\
# flightdeck runtime state — never commit an org's evidence (store + ledger)
.flightdeck/
"""

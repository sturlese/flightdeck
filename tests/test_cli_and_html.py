"""End-to-end CLI tests: the exact commands a new user types, via the runner."""

import csv
import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from flightdeck.backlog import ranked
from flightdeck.cli import app
from flightdeck.config import load_org
from flightdeck.ledger import Ledger
from flightdeck.metrics import build_report
from flightdeck.report import html as html_report
from flightdeck.store import Store

runner = CliRunner()


def invoke(*args: str):
    # Wide virtual terminal so rich never wraps mid-assertion.
    return runner.invoke(app, list(args), env={"COLUMNS": "220"})


def _init(tmp_path: Path) -> Path:
    root = tmp_path / "org"
    result = invoke("init", "--dir", str(root))
    assert result.exit_code == 0, result.output
    return root


def test_init_scaffolds_a_loadable_org(tmp_path):
    root = _init(tmp_path)
    org = load_org(root)  # the scaffold must satisfy the loader's strictness
    assert "meeting-minutes" in org.workflows
    assert "mock-fast" in org.models


def test_init_refuses_to_overwrite(tmp_path):
    root = _init(tmp_path)
    result = invoke("init", "--dir", str(root))
    assert result.exit_code == 2


def test_run_feedback_report_loop_offline(tmp_path):
    root = _init(tmp_path)

    result = invoke(
        "run", "meeting-minutes", "--dir", str(root),
        "--var", "notes=Decided to ship v2 on May 5.", "--user", "ana",
    )
    assert result.exit_code == 0, result.output
    assert "✓ completed" in result.output
    assert "close the loop" in result.output

    org = load_org(root)
    with Store(org.db_path) as store:
        run_id = store.latest_runs(1)[0].id

    result = invoke("feedback", run_id, "--outcome", "accepted", "--minutes", "1.5", "--dir", str(root))
    assert result.exit_code == 0, result.output

    result = invoke("report", "--dir", str(root), "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    entry = next(w for w in data["workflows"] if w["workflow_id"] == "meeting-minutes")
    assert entry["runs_completed"] == 1
    assert entry["accepted"] == 1
    assert entry["hours_saved"] > 0

    result = invoke("audit", "verify", "--dir", str(root))
    assert result.exit_code == 0
    assert "chain intact" in result.output


def test_report_csv_emits_a_parseable_finance_statement(tmp_path):
    root = _init(tmp_path)
    result = invoke(
        "run", "meeting-minutes", "--dir", str(root),
        "--var", "notes=Decided to ship v2 on May 5.", "--user", "ana",
    )
    assert result.exit_code == 0, result.output

    out = tmp_path / "statement.csv"
    result = invoke("report", "--dir", str(root), "--csv", str(out))
    assert result.exit_code == 0, result.output
    assert "finance statement" in result.output
    assert out.exists()

    with out.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        data_rows = list(reader)
    assert header == [
        "workflow_id", "workflow_name", "department", "month", "currency",
        "runs_completed", "reviewed", "reviewed_pct",
        "hours_saved", "value", "ai_cost", "net",
    ]
    row = next(r for r in data_rows if r[0] == "meeting-minutes")
    assert len(row) == len(header)
    assert row[4]  # currency is present
    assert int(row[5]) >= 1  # runs_completed
    # money and hours columns parse as fixed-decimal numbers
    for value in (row[7], row[8], row[9], row[10], row[11]):
        float(value)


def test_blocked_run_exits_nonzero_and_is_recorded(tmp_path):
    root = _init(tmp_path)
    workflow_path = root / "workflows" / "meeting-minutes.yaml"
    spec = yaml.safe_load(workflow_path.read_text())
    spec["data_classification"] = "restricted"  # starter policy: fails closed
    workflow_path.write_text(yaml.safe_dump(spec))

    result = invoke("run", "meeting-minutes", "--dir", str(root), "--var", "notes=x")
    assert result.exit_code == 1
    assert "blocked" in result.output

    org = load_org(root)
    with Store(org.db_path) as store:
        assert store.latest_runs(1)[0].status == "blocked"
    assert Ledger(org.ledger_path).entries()[-1]["event"] == "run_blocked"


def test_missing_variable_is_exit_2_with_the_needed_names(tmp_path):
    root = _init(tmp_path)
    result = invoke("run", "meeting-minutes", "--dir", str(root))
    assert result.exit_code == 2
    assert "notes" in result.output


def test_policy_check_names_the_route(tmp_path):
    root = _init(tmp_path)
    result = invoke("policy", "check", "meeting-minutes", "--dir", str(root))
    assert result.exit_code == 0, result.output
    assert "route →" in result.output
    assert "PII redaction" in result.output


def test_promote_scaffolds_a_loadable_workflow(tmp_path):
    root = _init(tmp_path)
    usecases = yaml.safe_load((root / "usecases.yaml").read_text())
    usecases["usecases"].append(
        {
            "id": "invoice-triage", "name": "Invoice triage", "department": "Finance",
            "task_minutes": 6, "tasks_per_month": 300, "automation_potential": 0.7,
            "data_readiness": 3, "process_stability": 4, "risk": 4, "effort_weeks": 2,
        }
    )
    (root / "usecases.yaml").write_text(yaml.safe_dump(usecases))

    result = invoke("promote", "invoice-triage", "--dir", str(root))
    assert result.exit_code == 0, result.output
    org = load_org(root)  # the scaffolded workflow must load
    workflow = org.workflows["invoice-triage"]
    assert workflow.data_classification == "confidential"  # risk 4 → confidential
    assert workflow.tier == "frontier"

    result = invoke("promote", "invoice-triage", "--dir", str(root))
    assert result.exit_code == 2  # never overwrite


def test_promote_quotes_yaml_hostile_freetext(tmp_path):
    # A use case whose name/department/description contain YAML metacharacters must
    # still scaffold a LOADABLE workflow — the fields are quoted, not interpolated
    # raw (else "Ops: EU" breaks the document and "Bug #42" is eaten as a comment).
    root = _init(tmp_path)
    usecases = yaml.safe_load((root / "usecases.yaml").read_text())
    usecases["usecases"].append(
        {
            "id": "ticket-routing", "name": "Bug #42\ntriage", "department": "Ops: EU",
            "description": "Route tickets: fast and safe",
            "task_minutes": 6, "tasks_per_month": 300, "automation_potential": 0.7,
            "data_readiness": 3, "process_stability": 4, "risk": 2, "effort_weeks": 1,
        }
    )
    (root / "usecases.yaml").write_text(yaml.safe_dump(usecases))

    result = invoke("promote", "ticket-routing", "--dir", str(root))
    assert result.exit_code == 0, result.output
    org = load_org(root)  # currently raises ConfigError (invalid YAML) before the fix
    workflow = org.workflows["ticket-routing"]
    # A "#" (comment), a ":" (mapping) and a newline (block-scalar break) all survive.
    assert workflow.name == "Bug #42\ntriage"  # not silently truncated to "Bug"
    assert workflow.department == "Ops: EU"  # colon didn't break the mapping
    assert workflow.description == "Route tickets: fast and safe"


def test_backlog_command_ranks(tmp_path):
    root = _init(tmp_path)
    result = invoke("backlog", "--dir", str(root))
    assert result.exit_code == 0
    assert "Meeting minutes" in result.output
    assert "promote the winner" in result.output


def test_audit_tail_n_zero_shows_nothing(tmp_path):
    root = _init(tmp_path)
    invoke("run", "meeting-minutes", "--dir", str(root), "--var", "notes=hello")

    # -n 0 must show NO entries — `entries[-0:]` is the whole list, not none.
    zero = invoke("audit", "tail", "-n", "0", "--dir", str(root))
    assert zero.exit_code == 0
    assert "run_" not in zero.output  # the run_completed event is not printed
    assert "ledger is empty" not in zero.output  # the ledger isn't empty, just truncated

    # -n N still shows the most recent entries.
    some = invoke("audit", "tail", "-n", "5", "--dir", str(root))
    assert "run_completed" in some.output


def test_audit_tail_reports_a_truly_empty_ledger(tmp_path):
    root = _init(tmp_path)  # scaffolded but nothing has run → no ledger yet
    result = invoke("audit", "tail", "--dir", str(root))
    assert result.exit_code == 0
    assert "ledger is empty" in result.output


# ------------------------------------------------------------------ dashboard


def _seed_complete_week(store):
    from datetime import timedelta

    from flightdeck.schemas import Feedback, Run
    from tests.conftest import NOW

    for index, when in enumerate((NOW - timedelta(days=10), NOW - timedelta(days=9))):
        store.add_run(
            Run(
                id=f"seed{index}", workflow_id="support-reply", user="ana", started_at=when,
                finished_at=when, status="completed", model_id="mock-fast-eu", provider="mock",
                tokens_in=900, tokens_out=200, cost=0.01, output="draft",
            )
        )
        store.add_feedback(Feedback(run_id=f"seed{index}", outcome="accepted", human_minutes=2, at=when))


def test_dashboard_is_self_contained_and_complete(org, store, ledger):
    _seed_complete_week(store)
    report = build_report(org, store, ledger)
    page = html_report.render(org, report, ranked(org))

    assert "<title>TestCo · flightdeck</title>" in page
    assert "audit ledger verified" in page
    assert "Support reply drafting" in page
    assert page.count("<svg") >= 4  # trend, spend, value, outcomes
    assert "{{" not in page.replace("{{ ", "KEEP")  # no unrendered template holes
    for banned in ("http://", "https://", "src=", "@import"):
        assert banned not in page, f"external reference sneaked in: {banned}"


def test_dashboard_escapes_hostile_workflow_names(org, store, ledger):
    hostile = org.workflows["support-reply"].model_copy(
        update={"name": '<script>alert(1)</script>', "id": "support-reply"}
    )
    org.workflows["support-reply"] = hostile
    report = build_report(org, store, ledger)
    page = html_report.render(org, report, [])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page or "\\u003cscript" in page

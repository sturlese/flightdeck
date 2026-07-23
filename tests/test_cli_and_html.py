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


def test_init_refuses_any_existing_org_file_and_writes_nothing(tmp_path):
    # models.yaml alone (no flightdeck.yaml) used to be silently overwritten.
    root = tmp_path / "org"
    root.mkdir()
    (root / "models.yaml").write_text("models: my own\n", encoding="utf-8")

    result = invoke("init", "--dir", str(root))
    assert result.exit_code == 2
    assert "models.yaml" in result.output
    assert (root / "models.yaml").read_text(encoding="utf-8") == "models: my own\n"
    assert not (root / "flightdeck.yaml").exists()  # refused before writing anything


def test_init_appends_to_an_existing_gitignore(tmp_path):
    # A project's .gitignore predates flightdeck: append the rule, never clobber.
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\n*.log", encoding="utf-8")  # no trailing \n

    result = invoke("init", "--dir", str(root))
    assert result.exit_code == 0, result.output
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "node_modules/" in lines  # the project's own rules survive
    assert "*.log" in lines  # last line intact, not glued to the appended block
    assert ".flightdeck/" in lines


def test_init_does_not_duplicate_an_existing_flightdeck_rule(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitignore").write_text("custom\n.flightdeck/\n", encoding="utf-8")

    result = invoke("init", "--dir", str(root))
    assert result.exit_code == 0, result.output
    text = (root / ".gitignore").read_text(encoding="utf-8")
    assert text.splitlines().count(".flightdeck/") == 1
    assert text.startswith("custom\n")


def test_demo_refuses_a_real_org_with_exit_2(tmp_path):
    root = _init(tmp_path)
    result = invoke("demo", "--dir", str(root))
    assert result.exit_code == 2
    assert "refusing to seed" in result.output
    org = load_org(root)  # the org still loads: nothing was overwritten
    assert "meeting-minutes" in org.workflows


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


def test_report_json_with_html_keeps_stdout_valid_json(tmp_path):
    # --json is documented "for pipelines": `report --json --html x > data.json` must
    # leave stdout a single valid JSON document. The "wrote dashboard/statement"
    # confirmations must go to stderr, not get appended after the JSON. Run the real
    # CLI in a subprocess so stdout/stderr are genuinely separate OS streams (the
    # in-process CliRunner does not separate rich's stderr console).
    import subprocess
    import sys

    root = _init(tmp_path)
    html_path = tmp_path / "dash.html"
    csv_path = tmp_path / "statement.csv"
    proc = subprocess.run(
        [sys.executable, "-c", "from flightdeck.cli import app; app()",
         "report", "--dir", str(root), "--json", "--html", str(html_path), "--csv", str(csv_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)  # stdout (fd 1) is clean JSON, not JSON + confirmations
    assert "workflows" in data
    assert "dashboard" in proc.stderr  # the confirmation went to stderr instead
    assert html_path.exists() and csv_path.exists()  # the files were still written


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


def test_backlog_survives_rich_markup_in_a_use_case_name(tmp_path):
    # Use-case names are operator free text; a name shaped like a rich closing tag
    # ("Purge [/tmp] files") must render literally, not be parsed as console markup
    # and crash the command with a MarkupError. The HTML report already escapes such
    # names; the terminal path must too.
    root = _init(tmp_path)
    usecases = yaml.safe_load((root / "usecases.yaml").read_text())
    usecases["usecases"][0]["name"] = "Purge [/tmp] scratch files"
    usecases["usecases"][0]["department"] = "Ops [/eng]"
    (root / "usecases.yaml").write_text(yaml.safe_dump(usecases))

    result = invoke("backlog", "--dir", str(root))
    assert result.exit_code == 0, result.output
    assert "/tmp" in result.output  # the name rendered instead of crashing


def test_report_survives_rich_markup_in_a_workflow_name(tmp_path):
    # The terminal report table renders the workflow name; a markup-shaped name must
    # not crash the headline `report` command.
    root = _init(tmp_path)
    workflow_path = root / "workflows" / "meeting-minutes.yaml"
    spec = yaml.safe_load(workflow_path.read_text())
    spec["name"] = "Notes [/x] drafting"
    workflow_path.write_text(yaml.safe_dump(spec))
    invoke("run", "meeting-minutes", "--dir", str(root), "--var", "notes=hello")

    result = invoke("report", "--dir", str(root))
    assert result.exit_code == 0, result.output


def test_terminal_report_escapes_markup_in_org_name_and_region_mix(org, store, ledger):
    # The report banner (org name) and the residency line (region_mix keys) are also
    # operator/registry free text rendered as rich markup on EVERY report — a
    # closing-tag fragment there must render literally, not raise MarkupError.
    import io

    from rich.console import Console

    from flightdeck.report import terminal

    report = build_report(org, store, ledger)
    report.org_name = "Acme [/tmp] Corp"
    report.governance.region_mix = {"eu [/x]": 1}
    console = Console(file=io.StringIO())
    terminal.render(report, [], console)  # must not raise MarkupError
    out = console.file.getvalue()
    assert "Acme" in out and "eu" in out


def test_policy_check_survives_markup_in_workflow_name(tmp_path):
    # `policy check` prints the workflow name/department as rich markup in its header.
    root = _init(tmp_path)
    workflow_path = root / "workflows" / "meeting-minutes.yaml"
    spec = yaml.safe_load(workflow_path.read_text())
    spec["name"] = "Minutes [/x]"
    workflow_path.write_text(yaml.safe_dump(spec))

    result = invoke("policy", "check", "meeting-minutes", "--dir", str(root))
    assert result.exit_code == 0, result.output
    assert "Minutes" in result.output


def test_policy_check_escapes_markup_in_data_rule_constraints(tmp_path):
    # The constraints summary joins operator-authored region/provider/model lists into
    # rich markup; a markup fragment there must render literally, not crash the command.
    from rich.errors import MarkupError

    root = _init(tmp_path)
    fd = yaml.safe_load((root / "flightdeck.yaml").read_text())
    fd.setdefault("policy", {}).setdefault("data_rules", {})["internal"] = {"regions": ["eu [/x]"]}
    (root / "flightdeck.yaml").write_text(yaml.safe_dump(fd))

    result = invoke("policy", "check", "meeting-minutes", "--dir", str(root))
    # No model matches the odd region, so it legitimately exits 1 — but it must render
    # the constraints line, not abort with a MarkupError.
    assert not isinstance(result.exception, MarkupError)
    assert "regions:" in result.output


def test_run_missing_var_error_survives_markup_in_a_var_name(tmp_path):
    # A declared step-var name is operator free text surfaced by the missing-variable
    # error path. A bracketed name must not turn that user error into a MarkupError crash.
    from rich.errors import MarkupError

    root = _init(tmp_path)
    wf_path = root / "workflows" / "meeting-minutes.yaml"
    spec = yaml.safe_load(wf_path.read_text())
    spec["steps"][0]["vars"].append("detail[/x]")  # declared but never passed
    wf_path.write_text(yaml.safe_dump(spec))

    result = invoke("run", "meeting-minutes", "--dir", str(root), "--var", "notes=hello")
    assert not isinstance(result.exception, MarkupError)
    assert result.exit_code == 2  # the clean "missing variable(s)" user error


def test_audit_tail_survives_markup_in_a_recorded_user(tmp_path):
    # The run actor (--user) is recorded in the ledger and rendered by `audit tail`
    # as part of the entry data; a markup fragment in it must not crash the command.
    from rich.errors import MarkupError

    root = _init(tmp_path)
    invoke("run", "meeting-minutes", "--dir", str(root), "--var", "notes=hi", "--user", "emp[/x]")

    result = invoke("audit", "tail", "-n", "5", "--dir", str(root))
    assert not isinstance(result.exception, MarkupError)
    assert result.exit_code == 0, result.output


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


def test_dashboard_workflow_net_uses_zero_decimals_like_the_terminal(org, store, ledger):
    # format.py promises a KPI reads the same in the terminal as on the mailed page.
    # The terminal net column and the net KPI tile both use money(value, currency, 0);
    # for a small net (|value| < 20) the HTML row must not fall on money()'s 2-decimal
    # default and show "€12.50" where the terminal shows "€12".
    from flightdeck.format import money

    _seed_complete_week(store)
    report = build_report(org, store, ledger)
    entry = next(e for e in report.workflows if e.workflow_id == "support-reply")
    entry.net_value = 12.5  # small net → triggers money()'s 2-decimal default
    page = html_report.render(org, report, ranked(org))

    assert money(12.5, report.currency, 0) in page  # "€12", the 0-decimal form
    assert money(12.5, report.currency) not in page  # never the 2-decimal "€12.50"


def test_money_never_renders_negative_zero():
    from flightdeck.format import money

    minus = "−"  # the U+2212 sign money() uses, not an ASCII hyphen
    # A tiny negative that rounds to zero (a near break-even net) reads as "0".
    assert money(-0.004, "EUR") == "€0.00"
    assert money(-0.004, "USD") == "$0.00"
    # Values that round to a nonzero magnitude keep their sign; zero stays unsigned.
    assert money(-0.006, "EUR") == f"{minus}€0.01"
    assert money(-3.0, "EUR") == f"{minus}€3.00"
    assert money(0.0, "EUR") == "€0"

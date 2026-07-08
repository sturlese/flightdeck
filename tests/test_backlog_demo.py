from flightdeck.backlog import ranked, score
from flightdeck.config import load_org
from flightdeck.demo import seed
from flightdeck.ledger import Ledger
from flightdeck.metrics import build_report
from flightdeck.store import Store


def test_demo_seeds_a_full_believable_program(tmp_path):
    summary = seed(tmp_path / "demo")

    assert summary.runs_completed > 800
    assert summary.runs_blocked > 5  # both incidents left traces
    assert summary.feedback > 400

    org = load_org(summary.root)
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        assert ledger.verify().ok  # the seeded history chains like real history
        report = build_report(org, store, ledger, days=30)

    assert report.total_hours_saved > 40
    assert report.total_net_value > 0
    assert report.governance.blocked_policy >= 1 or report.governance.blocked_budget >= 1
    assert report.governance.no_training_share == 1.0  # policy kept trainer models out
    health = {entry.workflow_id: entry.health for entry in report.workflows}
    assert health["localization-qa"] == "underperforming"  # the honest failure survives reporting


def test_demo_is_deterministic_for_a_given_day(tmp_path):
    first = seed(tmp_path / "one")
    second = seed(tmp_path / "two")
    assert (first.runs_completed, first.runs_blocked, first.runs_failed, first.feedback) == (
        second.runs_completed,
        second.runs_blocked,
        second.runs_failed,
        second.feedback,
    )


def test_backlog_ranking_orders_by_score(tmp_path):
    org = load_org(seed(tmp_path / "demo").root)
    scored = ranked(org)
    ids = [item.case.id for item in scored]

    assert set(ids) == {  # live/killed excluded: they are outcomes, not options
        "qa-bug-triage", "localization-qa", "invoice-coding",
        "jd-drafting", "chat-moderation-assist", "ua-creative-variants",
    }
    assert ids[0] == "qa-bug-triage"  # high volume, decent readiness, low risk
    assert scored[0].score > scored[-1].score

    moderation = next(item for item in scored if item.case.id == "chat-moderation-assist")
    assert moderation.risk_discount == 0.4  # risk 5 cuts the shiniest use case hard


def test_score_formula_matches_the_documented_arithmetic(tmp_path):
    org = load_org(seed(tmp_path / "demo").root)
    case = org.usecases["qa-bug-triage"]
    item = score(org, case)
    value = 8 / 60 * 1200 * 42.0 * 0.6
    assert item.monthly_value == value
    assert item.feasibility == (4 + 3) / 10
    assert item.risk_discount == 1 - (2 - 1) * 0.15
    assert item.score == value * 0.7 * 0.85 / 4

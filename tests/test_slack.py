"""The Slack review loop, offline and deterministic.

No socket is ever opened: the message builder and parser are pure, the apply
path funnels into ``record_feedback``, and posting takes an injected transport.
The headline tests prove a Slack click lands the IDENTICAL ledger event as
``flightdeck feedback`` — the whole point of the feature.
"""

import json
import urllib.parse

import pytest
from typer.testing import CliRunner

from flightdeck.cli import app
from flightdeck.config import load_org
from flightdeck.feedback import record_feedback
from flightdeck.integrations.slack import (
    SlackError,
    WebhookTransport,
    apply_interaction,
    build_minutes_modal,
    build_review_message,
    echo_transport,
    parse_interaction,
    parse_interaction_form,
    post_review,
)
from flightdeck.ledger import Ledger
from flightdeck.runner import execute
from flightdeck.schemas import Run
from flightdeck.store import Store
from tests.conftest import NOW, write_org

runner = CliRunner()


# --------------------------------------------------------------------- helpers


def _seed_run(org, store, ledger):
    return execute(org, org.workflows["support-reply"], {"ticket": "cannot log in"}, "ana", store, ledger, now=NOW)


def _blocks(message, block_type):
    return [b for b in message["blocks"] if b["type"] == block_type]


def _buttons(message):
    return _blocks(message, "actions")[0]["elements"]


def _button_payload(value: str, user: str = "ana") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U123", "username": user},
        "actions": [{"type": "button", "action_id": "flightdeck_feedback:accepted", "value": value}],
    }


def _view_submission_payload(run_id: str, outcome: str, minutes: str | None = None, user: str = "ana") -> dict:
    view = {"private_metadata": json.dumps({"run_id": run_id, "outcome": outcome})}
    if minutes is not None:
        view["state"] = {"values": {"minutes": {"value": {"type": "plain_text_input", "value": minutes}}}}
    return {"type": "view_submission", "user": {"username": user}, "view": view}


# --------------------------------------------------------------- build_review_message


def test_build_review_message_structure(org, store, ledger):
    run = _seed_run(org, store, ledger)
    message = build_review_message(run, org.workflows["support-reply"], org)

    assert json.loads(json.dumps(message)) == message  # JSON round-trips

    header = _blocks(message, "header")[0]["text"]["text"]
    assert "Support reply drafting" in header

    section = _blocks(message, "section")[0]["text"]["text"]
    assert run.output in section

    buttons = _buttons(message)
    assert len(buttons) == 3
    values = [json.loads(b["value"]) for b in buttons]
    assert all(v["run_id"] == run.id for v in values)
    assert {v["outcome"] for v in values} == {"accepted", "edited", "rejected"}


def test_build_review_message_truncates_long_output(org):
    run = Run(
        id="r1", workflow_id="support-reply", user="ana", started_at=NOW, finished_at=NOW,
        status="completed", model_id="mock-fast-eu", provider="mock",
        tokens_in=10, tokens_out=5, cost=0.01, output="x" * 5000,
    )
    message = build_review_message(run, org.workflows["support-reply"], org)
    section = _blocks(message, "section")[0]["text"]["text"]
    assert "…(truncated)" in section
    assert len(section) < 5000
    json.dumps(message)  # still serializable


# ----------------------------------------------------------------- parse_interaction


def test_parse_interaction_round_trips_a_button(org, store, ledger):
    run = _seed_run(org, store, ledger)
    message = build_review_message(run, org.workflows["support-reply"], org)
    accept_value = next(b["value"] for b in _buttons(message) if json.loads(b["value"])["outcome"] == "accepted")

    parsed = parse_interaction(_button_payload(accept_value, user="ana"))
    assert parsed.run_id == run.id
    assert parsed.outcome == "accepted"
    assert parsed.user == "ana"
    assert parsed.minutes is None


def test_parse_interaction_reads_modal_minutes():
    parsed = parse_interaction(_view_submission_payload("run-x", "edited", minutes="7"))
    assert parsed.run_id == "run-x"
    assert parsed.outcome == "edited"
    assert parsed.minutes == 7.0


def test_parse_interaction_modal_without_state_has_no_minutes():
    parsed = parse_interaction(_view_submission_payload("run-x", "edited"))  # no state key at all
    assert parsed.outcome == "edited" and parsed.minutes is None
    # And a state whose values aren't a mapping is tolerated the same way.
    payload = _view_submission_payload("run-x", "edited")
    payload["view"]["state"] = {"values": "unexpected"}
    assert parse_interaction(payload).minutes is None


def test_parse_interaction_falls_back_to_user_id():
    payload = _button_payload(json.dumps({"run_id": "r", "outcome": "accepted"}))
    payload["user"] = {"id": "U999"}  # no username
    assert parse_interaction(payload).user == "U999"


@pytest.mark.parametrize(
    "payload",
    [
        "not a dict",
        {},  # missing user
        {"user": {"username": "ana"}},  # no actions, not a view_submission
        {"user": {"username": "ana"}, "actions": [{"value": "not-json"}]},
        {"user": {"username": "ana"}, "actions": [{"value": json.dumps({"run_id": "r", "outcome": "nope"})}]},
        {"type": "view_submission", "user": {"username": "ana"}},  # no view
    ],
)
def test_parse_interaction_rejects_malformed_payload(payload):
    with pytest.raises(SlackError):
        parse_interaction(payload)


def test_build_minutes_modal_round_trips(org, store, ledger):
    run = _seed_run(org, store, ledger)
    view = build_minutes_modal(run.id, "edited", workflow_id="support-reply")
    payload = {
        "type": "view_submission",
        "user": {"username": "ana"},
        "view": {
            "private_metadata": view["private_metadata"],
            "state": {"values": {"minutes": {"value": {"type": "plain_text_input", "value": "3"}}}},
        },
    }
    parsed = parse_interaction(payload)
    assert parsed.run_id == run.id and parsed.outcome == "edited" and parsed.minutes == 3.0


# ------------------------------------------------------------- parse_interaction_form


def test_parse_interaction_form_accepts_json_text():
    payload = {"type": "block_actions", "user": {"username": "ana"}}
    assert parse_interaction_form(json.dumps(payload)) == payload


def test_parse_interaction_form_accepts_slack_urlencoded_body():
    payload = {"type": "block_actions", "user": {"username": "ana"}}
    body = "payload=" + urllib.parse.quote(json.dumps(payload))
    assert parse_interaction_form(body) == payload


def test_parse_interaction_form_accepts_dicts():
    payload = {"a": 1}
    assert parse_interaction_form({"payload": json.dumps(payload)}) == payload
    assert parse_interaction_form(payload) == payload  # bare dict passthrough


def test_parse_interaction_form_rejects_garbage():
    with pytest.raises(SlackError):
        parse_interaction_form("not json")
    with pytest.raises(SlackError):
        parse_interaction_form("")
    with pytest.raises(SlackError):
        parse_interaction_form(json.dumps([1, 2, 3]))  # JSON array, not an object


# ---------------------------------------------------------------- apply_interaction


def test_apply_interaction_matches_record_feedback(org, store, ledger):
    """A Slack Accept lands the IDENTICAL ledger event as the CLI feedback path."""
    run = _seed_run(org, store, ledger)

    payload = _button_payload(json.dumps({"run_id": run.id, "outcome": "accepted"}), user="ana")
    slack_fb = apply_interaction(payload, store, ledger, org, minutes=5)
    slack_event = ledger.entries()[-1]

    # The exact call the `flightdeck feedback` command makes.
    record_feedback(store, ledger, run.id, "accepted", human_minutes=5, by="ana")
    cli_event = ledger.entries()[-1]

    assert slack_event["event"] == cli_event["event"] == "feedback_recorded"
    assert slack_event["data"] == cli_event["data"]
    assert slack_fb.by == "ana" and slack_fb.human_minutes == 5 and slack_fb.note == "via slack"


def test_apply_interaction_reject_maps_and_minutes_default_none(org, store, ledger):
    run = _seed_run(org, store, ledger)
    payload = _button_payload(json.dumps({"run_id": run.id, "outcome": "rejected"}))
    fb = apply_interaction(payload, store, ledger, org)
    assert fb.outcome == "rejected"
    assert fb.human_minutes is None  # buttons carry no minutes → org default downstream


def test_apply_interaction_uses_modal_minutes(org, store, ledger):
    run = _seed_run(org, store, ledger)
    fb = apply_interaction(_view_submission_payload(run.id, "edited", minutes="9"), store, ledger, org)
    assert fb.outcome == "edited" and fb.human_minutes == 9.0


def test_apply_interaction_resolves_reviewer_through_the_directory(tmp_path):
    # A click by Slack handle and a CLI review by alias must land on the SAME
    # stable id — one person, one reviewer to the KPIs (same rule as `flightdeck
    # feedback`, which already resolves --by through the directory).
    directory = {
        "provider": "azure_ad",
        "users": [
            {
                "id": "AAD-42", "display_name": "Ana García",
                "email": "ana.garcia@example.com", "department": "Support",
                "aliases": ["ana", "ana.g"],
            }
        ],
    }
    org = load_org(write_org(tmp_path / "org", directory=directory))
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        run = _seed_run(org, store, ledger)
        payload = _button_payload(json.dumps({"run_id": run.id, "outcome": "accepted"}), user="ana.g")
        fb = apply_interaction(payload, store, ledger, org)

    assert fb.by == "AAD-42"  # the stable id, not the raw Slack handle
    assert ledger.entries()[-1]["data"]["by"] == "AAD-42"


def test_apply_interaction_keeps_unknown_reviewers_raw(org, store, ledger):
    # No directory (or no match) → the raw Slack string, exactly as before.
    run = _seed_run(org, store, ledger)
    payload = _button_payload(json.dumps({"run_id": run.id, "outcome": "accepted"}), user="stranger")
    fb = apply_interaction(payload, store, ledger, org)
    assert fb.by == "stranger"


# ------------------------------------------------------------------------ transport


def test_post_review_uses_injected_transport():
    sent = {}

    def fake(message):
        sent["message"] = message
        return {"ok": True}

    result = post_review({"blocks": []}, transport=fake)
    assert result == {"ok": True}
    assert sent["message"] == {"blocks": []}


def test_post_review_without_transport_raises_offline():
    with pytest.raises(SlackError, match="no Slack transport"):
        post_review({"blocks": []})


def test_echo_transport_does_not_send():
    out = echo_transport({"blocks": []})
    assert out == {"ok": True, "sent": False, "message": {"blocks": []}}


def test_webhook_transport_requires_a_url():
    with pytest.raises(SlackError):
        WebhookTransport("")


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_webhook_transport_posts_via_urllib(monkeypatch):
    """Exercise the real transport with urlopen faked — still no socket opened."""
    import urllib.request

    captured = {}

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["content_type"] = request.headers.get("Content-type")
        return _FakeResponse(200, b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = WebhookTransport("https://hooks.example/T/B/X")({"blocks": []})
    assert result == {"ok": True, "status": 200, "body": "ok"}
    assert captured["url"] == "https://hooks.example/T/B/X"
    assert captured["body"] == {"blocks": []}
    assert captured["content_type"] == "application/json"


def test_webhook_transport_wraps_network_error(monkeypatch):
    import urllib.error
    import urllib.request

    def boom(request):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(SlackError, match="failed to post"):
        WebhookTransport("https://hooks.example/T/B/X")({"blocks": []})


def test_parse_interaction_accepts_string_user():
    payload = _button_payload(json.dumps({"run_id": "r", "outcome": "accepted"}))
    payload["user"] = "ana"  # some payloads carry a bare user string
    assert parse_interaction(payload).user == "ana"


def test_parse_interaction_rejects_non_numeric_modal_minutes():
    with pytest.raises(SlackError, match="minutes must be a number"):
        parse_interaction(_view_submission_payload("run-x", "edited", minutes="soon"))


@pytest.mark.parametrize("bad", ["-5", "nan", "inf"])
def test_parse_interaction_rejects_invalid_modal_minutes(bad):
    # Negative / non-finite modal minutes are a malformed payload → SlackError, not
    # a pydantic ValidationError leaking past apply_interaction's documented contract.
    with pytest.raises(SlackError, match="minutes must be a non-negative number"):
        parse_interaction(_view_submission_payload("run-x", "edited", minutes=bad))


def test_parse_interaction_tolerates_empty_modal_state():
    # A view_submission with no filled inputs → minutes stays None (org default).
    payload = _view_submission_payload("run-x", "edited")
    payload["view"]["state"] = {"values": {"noise": "not-a-dict", "blank": {"x": {"value": ""}}}}
    parsed = parse_interaction(payload)
    assert parsed.minutes is None


def test_parse_interaction_form_rejects_unusable_types():
    with pytest.raises(SlackError):
        parse_interaction_form({"payload": [1, 2, 3]})  # payload is neither dict nor str


# ------------------------------------------------------------------------- CLI slack


def _seeded_org(tmp_path):
    root = tmp_path / "org"
    assert runner.invoke(app, ["init", "--dir", str(root)]).exit_code == 0
    result = runner.invoke(
        app, ["run", "meeting-minutes", "--dir", str(root), "--var", "notes=Ship v2"], env={"COLUMNS": "220"}
    )
    assert result.exit_code == 0, result.output
    org = load_org(root)
    with Store(org.db_path) as store:
        run_id = store.latest_runs(1)[0].id
    return root, run_id


def test_cli_slack_post_prints_block_kit_json_offline(tmp_path):
    root, run_id = _seeded_org(tmp_path)
    result = runner.invoke(app, ["slack", "post", run_id, "--dir", str(root)], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output

    message = json.loads(result.output)
    actions = next(b for b in message["blocks"] if b["type"] == "actions")
    assert len(actions["elements"]) == 3
    assert {json.loads(e["value"])["outcome"] for e in actions["elements"]} == {"accepted", "edited", "rejected"}


def test_cli_slack_post_unknown_run_exits_2(tmp_path):
    root, _ = _seeded_org(tmp_path)
    result = runner.invoke(app, ["slack", "post", "ghost", "--dir", str(root)])
    assert result.exit_code == 2


def test_cli_slack_handle_records_identically_to_feedback(tmp_path):
    root, run_id = _seeded_org(tmp_path)
    org = load_org(root)

    fb = runner.invoke(
        app, ["feedback", run_id, "--outcome", "accepted", "--minutes", "5", "--by", "ana", "--dir", str(root)]
    )
    assert fb.exit_code == 0, fb.output
    cli_event = Ledger(org.ledger_path).entries()[-1]

    payload = {
        "type": "block_actions",
        "user": {"id": "U1", "username": "ana"},
        "actions": [
            {
                "type": "button",
                "action_id": "flightdeck_feedback:accepted",
                "value": json.dumps({"run_id": run_id, "outcome": "accepted"}),
            }
        ],
    }
    result = runner.invoke(
        app, ["slack", "handle", "--minutes", "5", "--dir", str(root)], input=json.dumps(payload)
    )
    assert result.exit_code == 0, result.output
    assert "recorded via Slack" in result.output

    slack_event = Ledger(org.ledger_path).entries()[-1]
    assert slack_event["event"] == cli_event["event"] == "feedback_recorded"
    assert slack_event["data"] == cli_event["data"]


def test_cli_slack_handle_malformed_stdin_exits_2(tmp_path):
    root, _ = _seeded_org(tmp_path)
    result = runner.invoke(app, ["slack", "handle", "--dir", str(root)], input="not a payload")
    assert result.exit_code == 2


def test_cli_slack_handle_unknown_run_exits_2(tmp_path):
    root, _ = _seeded_org(tmp_path)
    payload = {
        "type": "block_actions",
        "user": {"username": "ana"},
        "actions": [{"value": json.dumps({"run_id": "ghost", "outcome": "accepted"})}],
    }
    result = runner.invoke(app, ["slack", "handle", "--dir", str(root)], input=json.dumps(payload))
    assert result.exit_code == 2


def test_cli_slack_handle_negative_modal_minutes_exits_2(tmp_path):
    root, run_id = _seeded_org(tmp_path)
    payload = _view_submission_payload(run_id, "edited", minutes="-5")
    result = runner.invoke(app, ["slack", "handle", "--dir", str(root)], input=json.dumps(payload))
    assert result.exit_code == 2  # was exit 1 with a leaked ValidationError traceback

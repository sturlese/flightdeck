"""Slack review loop — meet reviewers where they live, feed the same evidence.

Reviewers work in Slack, not a terminal, so this adapter posts a run's output to
a channel with Accept / Edited / Reject buttons and turns a click back into the
identical feedback the CLI records. The design keeps the CORE OFFLINE and
DETERMINISTIC:

- ``build_review_message`` is pure: a run in, a Slack Block Kit ``dict`` out
  (JSON-serializable, no I/O). The three buttons carry the ``run_id`` and the
  ``outcome`` in their ``value`` so the click is self-describing.
- ``parse_interaction`` reads Slack's interactive payload defensively — a
  malformed dict raises ``SlackError``, never a bare ``KeyError``.
- ``apply_interaction`` funnels into ``record_feedback`` — the SAME store row and
  ``feedback_recorded`` ledger event ``flightdeck feedback`` produces.
- ``post_review`` is the only thing that can touch the network, and only when a
  real ``transport`` is injected. ``urllib`` is imported inside the transport,
  never at module top level, so importing this module never pulls in networking
  and the offline path has no way to reach out.

Buttons alone cannot collect free-text minutes, so minutes are OPTIONAL: absent,
feedback falls back to the org's ``default_review_minutes`` (the conservative
model). A minutes-collection modal is provided for callers who want it, but the
button path stands on its own.
"""

import json
import math
import urllib.parse
from dataclasses import dataclass

from flightdeck.config import Org
from flightdeck.feedback import VALID_OUTCOMES, record_feedback
from flightdeck.ledger import Ledger
from flightdeck.report.html import money
from flightdeck.schemas import Feedback, Outcome, Run, Workflow
from flightdeck.store import Store

#: Shared prefix for the feedback buttons' ``action_id`` (Slack requires each
#: element's id to be unique within a block, so the outcome is appended).
FEEDBACK_ACTION = "flightdeck_feedback"

#: Slack section text is capped at 3000 chars; leave headroom for the code fence
#: and the truncation marker so a long output never trips the block limit.
OUTPUT_LIMIT = 2800

#: (outcome, button label), in the order they appear in the message.
_BUTTONS: tuple[tuple[Outcome, str], ...] = (
    ("accepted", "Accept"),
    ("edited", "Edited"),
    ("rejected", "Reject"),
)

#: Slack button styling. "edited" stays default (neutral).
_BUTTON_STYLE: dict[str, str] = {"accepted": "primary", "rejected": "danger"}


class SlackError(Exception):
    """A malformed Slack payload or a missing/failed transport — raised in place of
    a leaked ``KeyError`` or a raw networking exception, so callers can map it to a
    clean exit code or channel message."""


# ------------------------------------------------------------------ render (pure)


def _truncate(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    marker = "\n…(truncated)"
    return text[: max(0, limit - len(marker))] + marker


def build_review_message(run: Run, workflow: Workflow, org: Org) -> dict:
    """Render a run into a Slack Block Kit message (a JSON-serializable ``dict``).

    Header names the workflow; a context line carries the governance facts (model,
    cost, tokens, run id); a section shows the run output (truncated to stay under
    Slack's block limit); an actions block offers Accept / Edited / Reject. Each
    button encodes ``{run_id, outcome, workflow}`` in its ``value`` so the
    interaction handler can recover them with no server-side state.
    """
    currency = org.config.currency
    output = run.output or "(no output)"
    context_line = (
        f"model *{run.model_id or 'n/a'}* · {money(run.cost, currency)} · "
        f"{run.tokens_in:,}→{run.tokens_out:,} tok · run `{run.id}`"
    )
    actions = []
    for outcome, label in _BUTTONS:
        button = {
            "type": "button",
            "action_id": f"{FEEDBACK_ACTION}:{outcome}",
            "text": {"type": "plain_text", "text": label, "emoji": True},
            "value": json.dumps({"run_id": run.id, "outcome": outcome, "workflow": workflow.id}),
        }
        if outcome in _BUTTON_STYLE:
            button["style"] = _BUTTON_STYLE[outcome]
        actions.append(button)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Review: {workflow.name}", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_line}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{_truncate(output)}```"}},
        {"type": "actions", "block_id": FEEDBACK_ACTION, "elements": actions},
    ]
    # ``text`` is the notification/fallback shown where blocks can't render.
    return {"text": f"Review needed: {workflow.name} (run {run.id})", "blocks": blocks}


def build_minutes_modal(run_id: str, outcome: str, workflow_id: str | None = None) -> dict:
    """Optional: a modal view that collects the minutes a reviewer spent.

    The run/outcome ride in ``private_metadata`` (Slack echoes it back on submit),
    so a ``view_submission`` payload carries everything ``apply_interaction`` needs.
    Returned as a plain view dict; opening it is the caller's job (``views.open``).
    """
    return {
        "type": "modal",
        "callback_id": FEEDBACK_ACTION,
        "private_metadata": json.dumps({"run_id": run_id, "outcome": outcome, "workflow": workflow_id}),
        "title": {"type": "plain_text", "text": "Review time"},
        "submit": {"type": "plain_text", "text": "Record"},
        "blocks": [
            {
                "type": "input",
                "block_id": "minutes",
                "optional": True,
                "label": {"type": "plain_text", "text": "Minutes spent reviewing/fixing"},
                "element": {"type": "plain_text_input", "action_id": "value"},
            }
        ],
    }


# ------------------------------------------------------------------ parse (pure)


@dataclass
class ParsedInteraction:
    """The fields recovered from a Slack interaction, ready for ``record_feedback``."""

    run_id: str
    outcome: Outcome
    user: str
    minutes: float | None = None
    workflow_id: str | None = None


def _extract_user(payload: dict) -> str:
    user = payload.get("user")
    if isinstance(user, dict):
        # Prefer the human-readable handle for the ledger/reports; fall back to the
        # stable id (a real block_actions payload always carries at least the id).
        ident = user.get("username") or user.get("id")
        if ident:
            return str(ident)
    if isinstance(user, str) and user:
        return user
    raise SlackError("interaction payload is missing the Slack user")


def _decode(encoded: object) -> tuple[str, Outcome, str | None]:
    data = None
    if isinstance(encoded, str) and encoded:
        try:
            data = json.loads(encoded)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        raise SlackError("interaction carries no decodable run/outcome payload")
    run_id = data.get("run_id")
    outcome = data.get("outcome")
    if not run_id or outcome not in VALID_OUTCOMES:
        raise SlackError(f"interaction encodes an invalid run_id/outcome: {data!r}")
    return str(run_id), outcome, data.get("workflow")


def _extract_minutes(state: object) -> float | None:
    if not isinstance(state, dict):
        return None
    values = state.get("values")
    if not isinstance(values, dict):
        return None
    for block in values.values():
        if not isinstance(block, dict):
            continue
        for element in block.values():
            if isinstance(element, dict) and element.get("value") not in (None, ""):
                try:
                    minutes = float(element["value"])
                except (TypeError, ValueError):
                    raise SlackError(f"minutes must be a number, got {element['value']!r}") from None
                if not math.isfinite(minutes) or minutes < 0:
                    raise SlackError(f"minutes must be a non-negative number, got {element['value']!r}")
                return minutes
    return None


def parse_interaction(payload: dict) -> ParsedInteraction:
    """Recover ``(run_id, outcome, user[, minutes])`` from a Slack payload.

    Handles a button click (``block_actions``) and a modal submit
    (``view_submission``); anything malformed raises ``SlackError`` rather than
    leaking a ``KeyError``.
    """
    if not isinstance(payload, dict):
        raise SlackError("interaction payload must be a JSON object")
    user = _extract_user(payload)

    if payload.get("type") == "view_submission":
        view = payload.get("view")
        if not isinstance(view, dict):
            raise SlackError("view_submission payload has no view")
        run_id, outcome, workflow_id = _decode(view.get("private_metadata"))
        return ParsedInteraction(run_id, outcome, user, _extract_minutes(view.get("state")), workflow_id)

    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions or not isinstance(actions[0], dict):
        raise SlackError("interaction payload has no actionable button")
    run_id, outcome, workflow_id = _decode(actions[0].get("value"))
    return ParsedInteraction(run_id, outcome, user, None, workflow_id)


def parse_interaction_form(form: dict | str) -> dict:
    """Normalize whatever a caller pipes in into the interaction payload ``dict``.

    Accepts an already-parsed dict, the raw JSON of the payload, or Slack's
    ``application/x-www-form-urlencoded`` body (``payload=<json>``). Slack sends
    the last; a test or ``curl`` may send plain JSON.
    """
    if isinstance(form, dict):
        inner = form.get("payload", form)
        if isinstance(inner, dict):
            return inner
        form = inner  # a dict carrying the JSON string under "payload"
    if not isinstance(form, str):
        raise SlackError("interaction payload must be JSON text or a mapping")
    text = form.strip()
    if not text:
        raise SlackError("empty interaction payload")
    if text.startswith("payload="):
        parsed_form = urllib.parse.parse_qs(text)
        text = parsed_form.get("payload", [""])[0]
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise SlackError(f"interaction payload is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise SlackError("interaction payload must be a JSON object")
    return parsed


# ------------------------------------------------------------------ apply (shared path)


def apply_interaction(
    payload: dict,
    store: Store,
    ledger: Ledger,
    org: Org,
    minutes: float | None = None,
) -> Feedback:
    """Parse a Slack interaction and record it through the shared feedback path.

    An explicit ``minutes`` wins; otherwise a modal's minutes are used; otherwise
    ``None`` (metrics fall back to ``org.config.default_review_minutes``). ``org``
    is accepted for parity with the CLI and future directory resolution — today
    ``by`` is simply the Slack user string. Raises ``SlackError`` on a bad payload
    and ``FeedbackError`` (from ``record_feedback``) on an unknown run.
    """
    parsed = parse_interaction(payload)
    effective_minutes = minutes if minutes is not None else parsed.minutes
    return record_feedback(
        store,
        ledger,
        parsed.run_id,
        parsed.outcome,
        human_minutes=effective_minutes,
        by=parsed.user,
        note="via slack",
    )


# ------------------------------------------------------------------ transport (opt-in network)


def echo_transport(message: dict) -> dict:
    """Offline default transport: returns the message unsent, so demos and tests
    drive the whole post path without a network."""
    return {"ok": True, "sent": False, "message": message}


class WebhookTransport:
    """Posts a Block Kit message to a Slack incoming webhook via stdlib ``urllib``.

    This is the only code path that opens a socket, and only when constructed with
    a real URL. ``urllib`` is imported lazily inside ``__call__`` so merely
    importing this module never touches networking."""

    def __init__(self, url: str):
        if not url:
            raise SlackError("webhook transport needs a URL")
        self.url = url

    def __call__(self, message: dict) -> dict:
        import urllib.error
        import urllib.request

        data = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310 (operator-supplied config URL)
                body = response.read().decode("utf-8", "replace")
                return {"ok": 200 <= response.status < 300, "status": response.status, "body": body}
        except urllib.error.URLError as exc:
            raise SlackError(f"failed to post to Slack webhook: {exc}") from exc


def post_review(message: dict, *, transport=None) -> object:
    """Send a Block Kit message via the injected ``transport`` callable.

    Offline-first: with no transport this raises ``SlackError`` instead of
    reaching for a network. Pass ``echo_transport`` for a no-op, a
    ``WebhookTransport`` for a real POST, or any fake in tests.
    """
    if transport is None:
        raise SlackError("no Slack transport configured — pass a transport (e.g. echo_transport or WebhookTransport)")
    return transport(message)

"""Adapters that carry flightdeck's evidence loop to where reviewers already are.

An integration never owns business logic: it renders a run into another system's
shape and translates that system's replies back into the ONE shared feedback
path (``flightdeck.feedback.record_feedback``). Everything here is offline and
deterministic by default — any network is a thin, injectable transport, so the
demo and the tests exercise the full round-trip without a socket.
"""

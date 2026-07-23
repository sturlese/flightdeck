"""OpenAI adapter — vendor-response handling via an injected fake SDK.

The real ``openai`` SDK is an optional extra and not installed in CI; the adapter
imports it lazily inside ``complete``, so a fake module in ``sys.modules`` exercises
the parsing paths hermetically.
"""

import sys
import types

import pytest

from flightdeck.providers import ProviderError
from flightdeck.providers.openai import OpenAIProvider
from flightdeck.schemas import ModelSpec

SPEC = ModelSpec(
    id="gpt-x", provider="openai", model="gpt-x", tier="fast",
    input_cost_per_mtok=1.0, output_cost_per_mtok=2.0,
)


def _install_fake_openai(monkeypatch, response):
    """Register a fake ``openai`` module whose OpenAI().chat.completions.create
    returns ``response``."""
    class _Completions:
        def create(self, **kwargs):
            return response

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = _Chat()

    module = types.ModuleType("openai")
    module.OpenAI = _OpenAI
    monkeypatch.setitem(sys.modules, "openai", module)


def test_openai_empty_choices_raises_provider_error(monkeypatch):
    # A 200 response with no choices (Azure content filter, an OpenAI-compatible
    # gateway) must surface as the adapter's ProviderError contract, not a raw
    # IndexError that the runner records as an "unexpected IndexError" defect.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = types.SimpleNamespace(
        choices=[],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=0),
    )
    _install_fake_openai(monkeypatch, response)
    with pytest.raises(ProviderError):
        OpenAIProvider().complete(SPEC, "hello", 100)


def test_openai_normal_response_is_parsed(monkeypatch):
    # The happy path still returns a Completion with the text and token counts.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content="hi there"))
    response = types.SimpleNamespace(
        choices=[choice],
        usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=3),
    )
    _install_fake_openai(monkeypatch, response)
    completion = OpenAIProvider().complete(SPEC, "hello", 100)
    assert completion.text == "hi there"
    assert completion.tokens_in == 12
    assert completion.tokens_out == 3


def test_openai_missing_api_key_raises_provider_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _install_fake_openai(monkeypatch, types.SimpleNamespace(choices=[], usage=None))
    with pytest.raises(ProviderError):
        OpenAIProvider().complete(SPEC, "hello", 100)

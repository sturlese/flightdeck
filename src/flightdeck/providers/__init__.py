"""Provider adapters — the ONLY place flightdeck talks to a vendor.

The contract is one method: complete(model, prompt, max_output_tokens) →
(text, tokens_in, tokens_out). Deliberately smaller than any vendor SDK:
flightdeck is an adoption and governance layer, not an agent framework. Teams
that orchestrate with PydanticAI, LangGraph or a vendor's own SDK keep doing
so — a custom adapter (or the store API) is enough to bring those runs under
the same ledger and the same reports.

Vendor SDKs are optional extras. The core installs with zero network
dependencies and the mock provider makes every command demoable offline —
an adoption tool that needs API keys before it shows value would fail its
own adoption metrics.
"""

from dataclasses import dataclass
from typing import Protocol

from flightdeck.schemas import ModelSpec


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int


class Provider(Protocol):
    def complete(self, spec: ModelSpec, prompt: str, max_output_tokens: int) -> Completion: ...


class ProviderError(Exception):
    """Adapter-level failure with a human-actionable message (missing extra,
    missing API key, vendor error). The runner records it; it never crashes a report."""


def get_provider(name: str) -> Provider:
    if name == "mock":
        from flightdeck.providers.mock import MockProvider

        return MockProvider()
    if name == "anthropic":
        from flightdeck.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    if name == "openai":
        from flightdeck.providers.openai import OpenAIProvider

        return OpenAIProvider()
    raise ProviderError(
        f"unknown provider '{name}' — built-ins: mock, anthropic, openai "
        f"(see docs/architecture.md for writing an adapter)"
    )

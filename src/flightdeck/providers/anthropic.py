"""Anthropic adapter. Requires the optional extra: pip install flightdeck-ai[anthropic]

Auth comes from the standard ANTHROPIC_API_KEY environment variable. A registry
entry's base_url override supports gateways and regional deployments; usage
numbers come from the API response, never estimated.
"""

import os

from flightdeck.providers import Completion, ProviderError
from flightdeck.schemas import ModelSpec


class AnthropicProvider:
    def complete(self, spec: ModelSpec, prompt: str, max_output_tokens: int) -> Completion:
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ProviderError(
                "anthropic SDK not installed — pip install 'flightdeck-ai[anthropic]'"
            ) from None
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError("ANTHROPIC_API_KEY is not set")

        client = Anthropic(base_url=spec.base_url) if spec.base_url else Anthropic()
        try:
            message = client.messages.create(
                model=spec.model,
                max_tokens=max_output_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # vendor errors become governance-visible failed runs
            raise ProviderError(f"anthropic: {exc}") from exc

        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        return Completion(
            text=text,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
        )

"""OpenAI adapter. Requires the optional extra: pip install ai-flightdeck[openai]

Uses chat.completions for maximum endpoint compatibility: the same adapter
serves api.openai.com, Azure OpenAI and OpenAI-compatible gateways — point the
registry entry's base_url at the deployment and keep OPENAI_API_KEY in the
environment. Residency-constrained orgs typically register an Azure EU
deployment here and let the data rules do the rest.
"""

import os

from flightdeck.providers import Completion, ProviderError
from flightdeck.schemas import ModelSpec


class OpenAIProvider:
    def complete(self, spec: ModelSpec, prompt: str, max_output_tokens: int) -> Completion:
        try:
            from openai import OpenAI
        except ImportError:
            raise ProviderError(
                "openai SDK not installed — pip install 'ai-flightdeck[openai]'"
            ) from None
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError("OPENAI_API_KEY is not set")

        client = OpenAI(base_url=spec.base_url) if spec.base_url else OpenAI()
        try:
            response = client.chat.completions.create(
                model=spec.model,
                max_completion_tokens=max_output_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise ProviderError(f"openai: {exc}") from exc

        usage = response.usage
        if not response.choices:
            # An empty `choices` on a 200 — some OpenAI-compatible gateways/proxies
            # return one — is a vendor-side failure, not a flightdeck defect. Surface
            # it through the adapter's ProviderError contract with a human-actionable
            # message, never as a raw IndexError from choices[0].
            raise ProviderError("openai: response contained no choices")
        return Completion(
            text=response.choices[0].message.content or "",
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )

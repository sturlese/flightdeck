"""The offline provider — deterministic, free, and honest about being fake.

Exists so that every flightdeck feature (routing, policy, budgets, feedback,
reports, the full demo) can be exercised without an API key or a network
connection. Output is derived from a hash of the prompt: same input, same
output, same token counts — tests and demos stay reproducible.
"""

import hashlib
import re

from flightdeck.providers import Completion
from flightdeck.schemas import ModelSpec

_OPENERS = [
    "Draft prepared from the provided inputs.",
    "Summary of the key points, ready for review.",
    "Proposed response based on the source material.",
    "Structured brief assembled from the request.",
]


class MockProvider:
    def complete(self, spec: ModelSpec, prompt: str, max_output_tokens: int) -> Completion:
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        opener = _OPENERS[digest[0] % len(_OPENERS)]
        # Echo a few content words so chained steps and reviews have something real to look at.
        words = re.findall(r"[A-Za-zÀ-ÿ']{4,}", prompt)
        sample = " ".join(words[: 6 + digest[1] % 6]) or "the request"
        text = (
            f"[mock:{spec.model}] {opener}\n\n"
            f"Covers: {sample}.\n"
            f"- Point one derived from the input.\n"
            f"- Point two with the relevant caveat.\n"
            f"- Suggested next step for the reviewer."
        )
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = min(max_output_tokens, 90 + digest[2] % 90)
        return Completion(text=text, tokens_in=tokens_in, tokens_out=tokens_out)

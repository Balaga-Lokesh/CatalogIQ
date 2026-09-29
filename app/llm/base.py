"""The common interface every LLM provider implements.

A provider takes one product and returns the model's RAW text. It does not
parse or validate it: that is done once, in app/validation.py, so the mock
and the real LLM are checked by exactly the same rules.
"""

from abc import ABC, abstractmethod


class LLMError(Exception):
    """An LLM call failed (network error, rate limit, server error, ...).

    Providers raise only this type, so the retry logic has one thing to catch.
    """


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def enrich(self, raw_title: str, raw_description: str = "") -> str:
        """Return the raw text the model produced for this product.

        Raises LLMError if the call fails.
        """

    async def close(self) -> None:
        """Release resources (e.g. an HTTP connection pool). Default: nothing."""

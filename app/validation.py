"""Validate raw LLM output (requirement 5).

Output that isn't valid JSON, or has a category outside CATEGORIES, counts
as a failed attempt and is retried like any other failure.

Rule of thumb: FIX what is unambiguous (tag case, duplicates, too many tags,
"Unknown" brand, category case) because a retry costs another LLM call;
REJECT anything structural (bad JSON, missing title, unknown category)
because we must never guess an answer that isn't there.
"""

import json

from app.llm.base import LLMError
from app.schemas import CATEGORIES, MAX_TAGS, Enrichment

# "groceries" -> "Groceries": same category, just the wrong case.
_CATEGORY_BY_LOWER = {c.lower(): c for c in CATEGORIES}

# Ways an LLM says "I don't know the brand".
_NO_BRAND = {"", "null", "none", "unknown", "n/a", "na", "generic"}


class InvalidOutputError(LLMError):
    """The LLM answered, but the answer is unusable.

    A subclass of LLMError, so the retry loop treats it like any failed call.
    """


def validate_enrichment(raw: str) -> Enrichment:
    """Parse and check raw LLM text. Returns a clean Enrichment or raises
    InvalidOutputError with a message saying what was wrong."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidOutputError(f"output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidOutputError("output is not a JSON object")

    return Enrichment(
        clean_title=_check_title(data.get("clean_title")),
        category=_check_category(data.get("category")),
        brand=_check_brand(data.get("brand")),
        tags=_check_tags(data.get("tags")),
    )


def _check_title(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOutputError("clean_title must be a non-empty string")
    return " ".join(value.split())


def _check_category(value) -> str:
    canonical = _CATEGORY_BY_LOWER.get(value.strip().lower()) if isinstance(value, str) else None
    if canonical is None:
        raise InvalidOutputError(f"category {value!r} is not one of the allowed categories")
    return canonical


def _check_brand(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidOutputError("brand must be a string or null")
    brand = " ".join(value.split())
    return None if brand.lower() in _NO_BRAND else brand


def _check_tags(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise InvalidOutputError("tags must be a list of strings")
    tags = []
    for tag in value:
        tag = " ".join(tag.lower().split())
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]

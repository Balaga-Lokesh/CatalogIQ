"""Tests for LLM output validation (requirement 5)."""

import json

import pytest

from app.llm.base import LLMError
from app.validation import InvalidOutputError, validate_enrichment

GOOD = {
    "clean_title": "Amul Butter 500 g (Pack of 2)",
    "category": "Groceries",
    "brand": "Amul",
    "tags": ["butter", "dairy"],
}


def with_changes(**changes) -> str:
    return json.dumps({**GOOD, **changes})


def test_valid_output_passes():
    result = validate_enrichment(json.dumps(GOOD))
    assert result.clean_title == "Amul Butter 500 g (Pack of 2)"
    assert result.category == "Groceries"
    assert result.brand == "Amul"
    assert result.tags == ["butter", "dairy"]


# ---- rejected: these count as a failed attempt ------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '{"clean_title": "Amul Butter", ',          # cut off mid-answer
        "",
        '["a", "list", "not", "an", "object"]',
        with_changes(category="Dairy"),              # the brief's rule: outside the list
        with_changes(category=None),
        with_changes(clean_title=""),
        with_changes(clean_title="   "),
        with_changes(clean_title=42),
        with_changes(brand=123),
        with_changes(tags="butter, dairy"),          # a string, not a list
        with_changes(tags=["butter", 7]),
    ],
)
def test_bad_output_is_rejected(raw):
    with pytest.raises(InvalidOutputError):
        validate_enrichment(raw)


def test_missing_category_is_rejected():
    data = dict(GOOD)
    del data["category"]
    with pytest.raises(InvalidOutputError):
        validate_enrichment(json.dumps(data))


def test_invalid_output_counts_as_an_llm_error():
    # The retry loop catches LLMError, so bad output is retried like a failed call.
    with pytest.raises(LLMError):
        validate_enrichment("not json")


def test_error_message_says_what_was_wrong():
    with pytest.raises(InvalidOutputError, match="Dairy"):
        validate_enrichment(with_changes(category="Dairy"))


# ---- fixed: unambiguous, not worth another LLM call ---------------------------

def test_category_case_is_fixed():
    assert validate_enrichment(with_changes(category=" home & kitchen ")).category == "Home & Kitchen"


def test_tags_are_lowercased_deduplicated_and_capped_at_5():
    raw = with_changes(tags=["Butter", "butter", " DAIRY ", "", "a", "b", "c", "d"])
    assert validate_enrichment(raw).tags == ["butter", "dairy", "a", "b", "c"]


def test_missing_tags_become_empty_list():
    data = dict(GOOD)
    del data["tags"]
    assert validate_enrichment(json.dumps(data)).tags == []


@pytest.mark.parametrize("brand", [None, "", "Unknown", "null", "N/A", "generic"])
def test_unknown_brand_becomes_null(brand):
    assert validate_enrichment(with_changes(brand=brand)).brand is None


def test_title_whitespace_is_collapsed():
    assert validate_enrichment(with_changes(clean_title="  Amul   Butter ")).clean_title == "Amul Butter"


def test_extra_fields_are_ignored():
    assert validate_enrichment(with_changes(confidence=0.9)).category == "Groceries"

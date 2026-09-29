"""Shared constants and data shapes (enrichment output, product, job).

TODO: define the product/job objects exactly as the API contract in the
brief describes them.
"""

from dataclasses import dataclass, field

# The only categories the LLM is allowed to return (brief, "Enrichment output").
CATEGORIES = (
    "Groceries",
    "Beverages",
    "Personal Care",
    "Household",
    "Electronics",
    "Fashion",
    "Home & Kitchen",
    "Other",
)

MAX_TAGS = 5


@dataclass(frozen=True)
class Enrichment:
    """A validated LLM answer for one product."""

    clean_title: str
    category: str
    brand: str | None
    tags: list[str] = field(default_factory=list)

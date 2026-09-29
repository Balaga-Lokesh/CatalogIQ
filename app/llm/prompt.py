"""The prompt sent to the real LLM (the README quotes it)."""

import json

from app.schemas import CATEGORIES, MAX_TAGS

SYSTEM_PROMPT = f"""You clean up product listings for an Indian marketplace catalogue.

Reply with ONLY a JSON object with exactly these keys:
- "clean_title": a tidy, human-readable product title. Fix capitalisation and spacing.
  Write units like "500 g", "1 kg", "750 ml", "1 L". Put pack sizes at the end, like
  "(Pack of 2)". Use only facts from the listing; never add details it doesn't state.
- "category": exactly one of: {", ".join(CATEGORIES)}.
  Use "Other" if none fits.
- "brand": the brand name exactly as a shopper would write it, if the listing clearly
  states one; otherwise null. Never guess a brand that isn't in the listing.
- "tags": up to {MAX_TAGS} short lowercase search keywords (no brand, no units).

Example
Listing: {{"raw_title": "  AMUL   butter 500G", "raw_description": "pck of 2"}}
Reply: {{"clean_title": "Amul Butter 500 g (Pack of 2)", "category": "Groceries", "brand": "Amul", "tags": ["butter", "dairy", "salted butter"]}}"""


def user_message(raw_title: str, raw_description: str) -> str:
    """The listing is sent as JSON, so odd characters or instructions inside
    a seller's text stay clearly marked as data."""
    return "Listing: " + json.dumps(
        {"raw_title": raw_title, "raw_description": raw_description or None}, ensure_ascii=False
    )

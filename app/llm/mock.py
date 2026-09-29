"""Mock provider (requirement 1) - the one the graders test with.

Behaviour: waits MOCK_LATENCY_MS, then fails with probability
MOCK_FAILURE_RATE, otherwise returns valid enrichment JSON.

The answer itself comes from simple keyword rules. It is deterministic:
the same product always gets the same answer (only failures are random).
"""

import asyncio
import json
import random
import re

from app.llm.base import LLMError, LLMProvider
from app.schemas import MAX_TAGS

# First matching category wins, so more specific categories come first.
CATEGORY_KEYWORDS = {
    "Beverages": ["tea", "coffee", "juice", "cola", "soda", "drink", "water", "milkshake", "energy"],
    "Personal Care": ["shampoo", "soap", "toothpaste", "cream", "lotion", "deodorant", "facewash",
                      "conditioner", "razor", "sunscreen", "hair", "skin"],
    "Household": ["detergent", "cleaner", "dishwash", "floor", "toilet", "mop", "bleach",
                  "freshener", "garbage", "tissue"],
    "Electronics": ["phone", "charger", "cable", "earphones", "earbuds", "headphones", "speaker",
                    "laptop", "mouse", "keyboard", "watch", "battery", "usb", "bluetooth"],
    "Fashion": ["shirt", "tshirt", "t-shirt", "jeans", "kurta", "saree", "dress", "shoes",
                "sneakers", "socks", "jacket", "cotton"],
    "Home & Kitchen": ["pan", "cooker", "bottle", "container", "bedsheet", "pillow", "curtain",
                       "knife", "mixer", "kadai", "tawa", "lamp"],
    "Groceries": ["rice", "atta", "flour", "dal", "oil", "sugar", "salt", "butter", "ghee", "milk",
                  "biscuits", "noodles", "masala", "spices", "paneer", "cheese", "bread", "chips"],
}

KNOWN_BRANDS = [
    "Amul", "Tata", "Nestle", "Britannia", "Parle", "Haldiram", "MDH", "Everest", "Fortune",
    "Aashirvaad", "Maggi", "Colgate", "Dove", "Lux", "Dettol", "Himalaya", "Nivea", "Surf Excel",
    "Vim", "Harpic", "Lizol", "Samsung", "Apple", "boAt", "Noise", "Mi", "Levis", "Puma", "Nike",
    "Adidas", "Prestige", "Milton", "Pigeon", "Red Label", "Bru", "Coca Cola", "Pepsi", "Real",
]

UNITS = {"g": "g", "gm": "g", "gms": "g", "kg": "kg", "ml": "ml", "l": "L", "ltr": "L"}
STOPWORDS = {"of", "the", "and", "for", "with", "pack", "pck", "pk", "new", "combo", "set"}


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, latency_ms: int, failure_rate: float, rng: random.Random | None = None):
        self.latency_s = latency_ms / 1000
        self.failure_rate = failure_rate
        # Injectable so tests can use a seeded generator and get repeatable results.
        self._rng = rng or random.Random()

    async def enrich(self, raw_title: str, raw_description: str = "") -> str:
        # asyncio.sleep, NOT time.sleep: it pauses only this call and lets the
        # event loop run other calls meanwhile. time.sleep would freeze everything.
        await asyncio.sleep(self.latency_s)
        if self._rng.random() < self.failure_rate:
            raise LLMError("mock: simulated LLM failure")
        return json.dumps(fake_enrichment(raw_title, raw_description or ""))


# ---------------------------------------------------------------------------
# The fake answer
# ---------------------------------------------------------------------------

def fake_enrichment(raw_title: str, raw_description: str) -> dict:
    words = _words(f"{raw_title} {raw_description}")
    brand = _find_brand(raw_title)
    return {
        "clean_title": _clean_title(raw_title, raw_description),
        "category": _pick_category(words),
        "brand": brand,
        "tags": _pick_tags(words, brand),
    }


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z\-]*", text.lower())


def _pick_category(words: list[str]) -> str:
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(k in words for k in keywords):
            return category
    return "Other"


def _find_brand(raw_title: str) -> str | None:
    title = " ".join(raw_title.lower().split())
    for brand in KNOWN_BRANDS:
        if title.startswith(brand.lower() + " ") or title == brand.lower():
            return brand
    return None


def _clean_title(raw_title: str, raw_description: str) -> str:
    """'  AMUL   butter 500G' + 'pck of 2'  ->  'Amul Butter 500 g (Pack of 2)'"""
    title = " ".join(raw_title.split())
    # "500G" / "500 gm" -> "500 g"
    title = re.sub(
        r"(\d+(?:\.\d+)?)\s*(kg|gms|gm|g|ml|ltr|l)\b",
        lambda m: f"{m.group(1)} {UNITS[m.group(2).lower()]}",
        title,
        flags=re.IGNORECASE,
    )
    tokens = title.split(" ")
    out = []
    for i, tok in enumerate(tokens):
        after_number = i > 0 and tokens[i - 1].replace(".", "").isdigit()
        if after_number and tok in UNITS.values():
            out.append(tok)  # keep the unit as normalised above
        elif tok.isalpha():
            out.append(tok.capitalize())
        else:
            out.append(tok)
    clean = " ".join(out)

    pack = re.search(r"\b(?:pack|pck|pk)\s*(?:of)?\s*(\d+)", f"{raw_title} {raw_description}", re.IGNORECASE)
    if pack:
        clean = re.sub(r"\s*\b(?:pack|pck|pk)\s*(?:of)?\s*\d+", "", clean, flags=re.IGNORECASE).strip()
        clean += f" (Pack of {pack.group(1)})"
    return clean


def _pick_tags(words: list[str], brand: str | None) -> list[str]:
    brand_words = set(brand.lower().split()) if brand else set()
    tags = []
    for w in words:
        if len(w) < 3 or w in STOPWORDS or w in brand_words or w in UNITS or w in tags:
            continue
        tags.append(w)
        if len(tags) == MAX_TAGS:
            break
    return tags

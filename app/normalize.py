"""Content key used for de-duplication (requirement 6).

Two products have the same content if raw_title + " " + raw_description
matches after lowercasing and collapsing whitespace.
"""

import hashlib


def normalize_content(raw_title: str, raw_description: str | None = None) -> str:
    """'  AMUL   butter 500G' + 'pck of 2'  ->  'amul butter 500g pck of 2'

    str.split() with no argument splits on ANY run of whitespace (spaces,
    tabs, newlines) and ignores it at both ends, so joining with one space
    collapses it. A missing description leaves a trailing space, which the
    same step removes: "title " and "title" end up identical.
    """
    text = f"{raw_title} {raw_description or ''}".lower()
    return " ".join(text.split())


def content_key(raw_title: str, raw_description: str | None = None) -> str:
    """SHA-256 of the normalised content: always 64 hex characters, however
    long the description, so it is cheap to store and index."""
    normalized = normalize_content(raw_title, raw_description)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

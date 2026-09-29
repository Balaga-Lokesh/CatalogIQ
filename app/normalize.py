"""Content key used for de-duplication (requirement 6).

Two products have the same content if raw_title + " " + raw_description
matches after lowercasing and collapsing whitespace.

TODO: content_key(raw_title, raw_description) -> str
"""

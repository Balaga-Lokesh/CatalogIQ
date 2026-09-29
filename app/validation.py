"""Validate raw LLM output (requirement 5).

Output that isn't valid JSON, or has a category outside CATEGORIES,
counts as a failed attempt (and is retried like any other failure).

TODO: parse + validate, raise an error on bad output.
"""

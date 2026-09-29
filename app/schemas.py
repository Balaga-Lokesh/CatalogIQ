"""Shared constants and data shapes (enrichment output, product, job).

TODO: define the enrichment result shape and the product/job objects
exactly as the API contract in the brief describes them.
"""

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

"""Tests for the de-duplication content key (requirement 6)."""

import pytest

from app.normalize import content_key, normalize_content


@pytest.mark.parametrize(
    "title, description, expected",
    [
        ("  AMUL   butter 500G", "pck of 2", "amul butter 500g pck of 2"),
        ("Amul\tButter\n500G", None, "amul butter 500g"),     # tabs and newlines collapse too
        ("Amul Butter", "", "amul butter"),                  # empty description: no trailing space
        ("Amul Butter", None, "amul butter"),                # missing description: same thing
    ],
)
def test_normalize_content(title, description, expected):
    assert normalize_content(title, description) == expected


def test_same_content_same_key():
    assert content_key("  AMUL   butter 500G", "pck of 2") == content_key("amul butter 500g", "PCK OF 2")


def test_different_content_different_key():
    assert content_key("Amul butter 500g") != content_key("Amul butter 100g")


def test_title_and_description_boundary_follows_the_brief():
    # The brief joins title + " " + description, so where the split falls
    # doesn't matter: these two are the same content.
    assert content_key("Amul butter", "500g") == content_key("Amul butter 500g", None)


def test_key_is_fixed_length_hex():
    key = content_key("x" * 10_000, "y" * 10_000)
    assert len(key) == 64
    int(key, 16)  # raises if not hex

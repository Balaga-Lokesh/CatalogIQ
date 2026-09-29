"""Tests for settings and the .env file loader."""

import os

from app.config import load_env_file


def test_env_file_fills_missing_variables(tmp_path, monkeypatch):
    for name in ("CIQ_A", "CIQ_B", "CIQ_C", "CIQ_EMPTY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "CIQ_A=plain\n"
        'CIQ_B="quoted value"\n'
        "CIQ_C = spaced \n"
        "CIQ_EMPTY=\n"
        "not a setting\n",
        encoding="utf-8",
    )
    load_env_file(env)
    assert os.environ["CIQ_A"] == "plain"
    assert os.environ["CIQ_B"] == "quoted value"
    assert os.environ["CIQ_C"] == "spaced"
    assert "CIQ_EMPTY" not in os.environ          # empty means "not set"
    for name in ("CIQ_A", "CIQ_B", "CIQ_C"):
        monkeypatch.delenv(name)


def test_real_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CIQ_A", "from-shell")
    env = tmp_path / ".env"
    env.write_text("CIQ_A=from-file\n", encoding="utf-8")
    load_env_file(env)
    assert os.environ["CIQ_A"] == "from-shell"


def test_missing_env_file_is_fine(tmp_path):
    load_env_file(tmp_path / "does-not-exist")

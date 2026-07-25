"""
Tests for per-capability AI mode and the cost meter.

Mode must resolve saved-override > env > config default, and never yield
anything but "standard"/"ai". The cost meter must charge nothing for a local
model, compute a real figure for a priced cloud model, and refuse to invent one
for an unpriced model.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_settings
import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    import db_handler
    db_handler.init_db()
    return db_handler


# ======================================================
# MODE RESOLUTION
# ======================================================
def test_default_mode_is_standard(db, monkeypatch):
    monkeypatch.delenv("AI_MODE_SUMMARY", raising=False)
    assert app_settings.mode_for("summary") == "standard"


def test_env_override_is_honoured(db, monkeypatch):
    monkeypatch.setenv("AI_MODE_SUMMARY", "ai")
    assert app_settings.mode_for("summary") == "ai"


def test_a_saved_override_beats_env(db, monkeypatch):
    monkeypatch.setenv("AI_MODE_SUMMARY", "standard")
    app_settings.set_mode("summary", "ai")
    assert app_settings.mode_for("summary") == "ai"


def test_an_invalid_env_value_falls_through_to_default(db, monkeypatch):
    monkeypatch.setenv("AI_MODE_SUMMARY", "banana")
    assert app_settings.mode_for("summary") == "standard"


def test_set_mode_rejects_an_invalid_mode(db):
    with pytest.raises(ValueError):
        app_settings.set_mode("summary", "turbo")


def test_all_modes_covers_every_capability(db, monkeypatch):
    for capability in app_settings.CAPABILITIES:
        monkeypatch.delenv(f"AI_MODE_{capability.upper()}", raising=False)
    modes = app_settings.all_modes()
    assert set(modes) == set(app_settings.CAPABILITIES)
    assert all(mode in ("standard", "ai") for mode in modes.values())


# ======================================================
# COST METER
# ======================================================
def _usage(**overrides):
    base = {"calls": 10, "input_tokens": 1_000_000, "output_tokens": 500_000}
    return {**base, **overrides}


def test_a_local_model_costs_nothing(monkeypatch):
    monkeypatch.setattr("db_handler.ai_usage", lambda: _usage())
    monkeypatch.setattr(config, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AI_MODEL", "llama3.1")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    result = app_settings.usage_summary()
    assert result["local"] is True
    assert result["cost_usd"] == 0.0


def test_a_priced_cloud_model_is_estimated(monkeypatch):
    monkeypatch.setattr("db_handler.ai_usage", lambda: _usage())
    monkeypatch.setattr(config, "AI_PROVIDER", "claude")
    monkeypatch.setattr(config, "AI_MODEL", "claude-opus-4-8")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    result = app_settings.usage_summary()
    # 1M input @ $5 + 0.5M output @ $25 = 5 + 12.5 = 17.5
    assert result["cost_usd"] == pytest.approx(17.5)


def test_an_unpriced_model_shows_no_dollar_figure(monkeypatch):
    monkeypatch.setattr("db_handler.ai_usage", lambda: _usage())
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "AI_MODEL", "some-unknown-model")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    result = app_settings.usage_summary()
    assert result["local"] is False
    assert result["cost_usd"] is None

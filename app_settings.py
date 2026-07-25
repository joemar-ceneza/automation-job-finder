"""
app_settings.py
Per-capability AI mode and the cost meter — the two pieces of §2.6/§2.7 that let
you decide, once, which features run AI by default and see what that has cost.

Mode is resolved in three layers, most specific first: a choice saved from the
dashboard (stored per database), then an .env override (AI_MODE_<CAP>), then the
config.MODES default. Standard is the floor — an unknown value or a mode of "ai"
with no provider still degrades to the deterministic path, so nothing here can
break a feature, only change its default.

The cost meter reads the AI cache (every call's token counts) and multiplies by
the configured model's list price. Local models are free, so their estimate is
zero; an unpriced model shows tokens without a dollar figure rather than a wrong
one.
"""
import logging
import os

import config
import db_handler

# The capabilities that have both a Standard and an AI implementation.
CAPABILITIES = ("explain", "summary", "cover_letter", "interview", "salary",
                "learning", "portfolio", "company")
_VALID_MODES = ("standard", "ai")
_META_PREFIX = "mode_"


# ======================================================
# MODE RESOLUTION
# ======================================================
def _stored_mode(capability: str) -> str | None:
    """The dashboard-saved override for a capability, if any."""
    try:
        value = db_handler.get_meta(_META_PREFIX + capability)
    except Exception:
        # No database yet (fresh CLI call before init) — fall through to env.
        return None
    return value if value in _VALID_MODES else None


def mode_for(capability: str) -> str:
    """
    The effective mode for a capability: saved override, else .env override,
    else the config default, else "standard".
    """
    stored = _stored_mode(capability)
    if stored:
        return stored
    env = os.getenv(f"AI_MODE_{capability.upper()}", "").strip().lower()
    if env in _VALID_MODES:
        return env
    default = config.MODES.get(capability, "standard")
    return default if default in _VALID_MODES else "standard"


def set_mode(capability: str, mode: str) -> None:
    """Saves a per-capability mode override (persists across restarts)."""
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
    db_handler.set_meta(_META_PREFIX + capability, mode)
    logging.info("Set %s mode to %s.", capability, mode)


def all_modes() -> dict[str, str]:
    """The effective mode for every capability, for the settings panel."""
    return {capability: mode_for(capability) for capability in CAPABILITIES}


# ======================================================
# COST / USAGE METER
# ======================================================
def _active_provider() -> str:
    return (os.getenv("AI_PROVIDER") or config.AI_PROVIDER or "none").lower()


def _active_model() -> str:
    return os.getenv("AI_MODEL") or config.AI_MODEL


def _is_local(provider: str) -> bool:
    """Local runtimes (and no provider) never cost money."""
    return provider in ("", "none", "off", "ollama", "lmstudio",
                        "openai_compatible")


def usage_summary() -> dict:
    """
    Lifetime AI usage plus a cost estimate. `cost_usd` is 0.0 for a local
    provider, a float for a priced cloud model, and None when the model's price
    isn't known — so the meter never shows a fabricated figure.
    """
    usage = db_handler.ai_usage()
    provider = _active_provider()
    model = _active_model()
    local = _is_local(provider)

    if local:
        cost = 0.0
    elif model in config.AI_PRICES:
        price_in, price_out = config.AI_PRICES[model]
        cost = (usage["input_tokens"] / 1_000_000 * price_in
                + usage["output_tokens"] / 1_000_000 * price_out)
    else:
        cost = None

    return {
        "calls": usage["calls"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "provider": provider,
        "model": model,
        "local": local,
        "cost_usd": cost,
    }

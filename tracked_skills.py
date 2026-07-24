"""
tracked_skills.py
The store of skills you've approved for tracking beyond config.MASTER_SKILLS.

Feature 16 proposes; this is where an approved proposal is kept. A skill added
here is honoured by the extractor exactly like a built-in one, so its demand
starts showing in the analytics — but it lives in the database (per corpus),
not in code, so approving a suggestion never edits config.py and is trivially
reversible.

Kept deliberately small: additions are (canonical, category, aliases), the same
shape the extractor and the lexicon already use, so nothing downstream needs a
special case for an approved skill versus a built-in one.
"""
import json
import logging

import db_handler

_META_KEY = "tracked_skill_additions"


def _load() -> list[dict]:
    raw = db_handler.get_meta(_META_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        logging.warning("Stored tracked-skill additions were unreadable — "
                        "ignoring them.")
        return []


def additions() -> list[tuple[str, str, tuple[str, ...]]]:
    """
    Approved extra skills as (canonical, category, aliases) tuples, ready to
    hand to skill_extractor. This is the whole public surface the extractor
    needs.
    """
    return [(entry["canonical"], entry.get("category", "tool"),
             tuple(entry.get("aliases", [])))
            for entry in _load()
            if entry.get("canonical")]


def is_tracked(canonical: str) -> bool:
    """True when this skill has already been approved."""
    target = canonical.strip().lower()
    return any(entry["canonical"].strip().lower() == target
               for entry in _load())


def add(canonical: str, category: str = "tool",
        aliases: tuple[str, ...] | list[str] = ()) -> bool:
    """
    Approves a skill for tracking. Returns False if it was already approved
    (idempotent), True when newly added. Does not re-extract — the caller
    decides when to rebuild job_skills, so a batch of approvals costs one pass.
    """
    if is_tracked(canonical):
        return False
    entries = _load()
    entries.append({"canonical": canonical, "category": category,
                    "aliases": list(aliases)})
    db_handler.set_meta(_META_KEY, json.dumps(entries))
    logging.info("Now tracking '%s' (%s) in addition to the built-in skills.",
                 canonical, category)
    return True


def remove(canonical: str) -> bool:
    """Stops tracking a previously-approved skill. Returns True if removed."""
    target = canonical.strip().lower()
    entries = _load()
    kept = [entry for entry in entries
            if entry["canonical"].strip().lower() != target]
    if len(kept) == len(entries):
        return False
    db_handler.set_meta(_META_KEY, json.dumps(kept))
    return True

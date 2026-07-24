"""
skill_proposals.py
Feature 16 — propose, never write. Scans your corpus for skills that ads
clearly ask for but your extraction dictionary does not track, and suggests
adding them, with the occurrence count that justifies each. You approve; only
then is anything tracked, and job_skills is rebuilt in the same action.

Why this matters: the extractor only ever reports config.MASTER_SKILLS, so a
skill absent from that list is invisible in the analytics no matter how often
it appears. Left to manual maintenance the dictionary drifts from how ads
actually phrase things — exactly the alias drift the codebase already suffered.
This turns that maintenance into an evidence-backed suggestion you accept or
decline, rather than a list you have to remember to hand-edit.

Deterministic end to end: candidates come from a curated lexicon (a human put
every name there), counts come from arithmetic over the corpus, and a proposal
appears only when a lexicon skill is both genuinely present AND not already
tracked. No model decides what counts as a skill.
"""
import re
from dataclasses import dataclass, field

import config
from skill_lexicon import LEXICON


@dataclass
class SkillProposal:
    """A skill your corpus asks for that you don't yet track."""
    canonical: str
    category: str
    occurrences: int                              # distinct jobs mentioning it
    merge_from: list[str] = field(default_factory=list)   # surface forms seen
    rationale: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _normalise(name: str) -> str:
    """Collapse a skill name to a comparison key: 'React.js' == 'React JS'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _tracked_norms(
        extra: list[tuple[str, str, tuple[str, ...]]] | None = None) -> set[str]:
    """
    Every normalised form already covered — MASTER_SKILLS and their aliases,
    plus any approved extras — so a candidate already tracked under any surface
    form is never re-proposed.
    """
    norms: set[str] = set()
    for skill in config.MASTER_SKILLS:
        norms.add(_normalise(skill))
        for alias in config.SKILL_ALIASES.get(skill, []):
            norms.add(_normalise(alias))
    for canonical, _category, aliases in (extra or []):
        norms.add(_normalise(canonical))
        for alias in aliases:
            norms.add(_normalise(alias))
    return norms


def _term_in_text(term: str, text_lower: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)"
    return bool(re.search(pattern, text_lower))


def _job_text(job: dict) -> str:
    return " ".join(part for part in (job.get("title", ""),
                                      job.get("description", ""),
                                      job.get("teaser", "")) if part).lower()


# ======================================================
# PUBLIC API
# ======================================================
def propose(jobs: list[dict], min_occurrences: int = 3,
            extra_tracked: list[tuple[str, str, tuple[str, ...]]] | None = None
            ) -> list[SkillProposal]:
    """
    Returns skills the corpus asks for that aren't tracked, most in-demand
    first. A candidate must be in the lexicon, appear in at least
    min_occurrences distinct jobs, and not already be tracked under any surface
    form. Pure over `jobs`; the caller supplies the corpus and any approvals.
    """
    tracked = _tracked_norms(extra_tracked)
    texts = [_job_text(job) for job in jobs]

    proposals = []
    for canonical, category, variants in LEXICON:
        terms = (canonical, *variants)
        if any(_normalise(term) in tracked for term in terms):
            continue                                    # already covered

        seen: set[str] = set()
        count = 0
        for text in texts:
            hits = [term for term in terms if _term_in_text(term, text)]
            if hits:
                count += 1
                seen.update(hits)
        if count >= min_occurrences:
            proposals.append(SkillProposal(
                canonical=canonical, category=category, occurrences=count,
                merge_from=sorted(seen),
                rationale=f"Appears in {count} tracked job(s) but isn't in your "
                          "skill dictionary."))

    proposals.sort(key=lambda proposal: proposal.occurrences, reverse=True)
    return proposals

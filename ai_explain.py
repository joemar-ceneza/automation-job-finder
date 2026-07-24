"""
ai_explain.py
AI mode for the score explanation. The model is handed the deterministic facts
and asked only to narrate them — it never recomputes a number, and a reply that
contradicts the ground truth is rejected rather than shown.

This is the pattern the whole AI layer follows: Standard mode computes the
truth, AI mode explains it. The payoff is threefold — the model cannot invent a
match percentage because it is given one; the prompt carries a compact fact
block instead of the full resume, roughly halving input tokens; and because the
facts came from arithmetic, a contradicting narrative is detectable in code.
"""
import json
import logging
from dataclasses import dataclass, field

import explain
from explain import ScoreExplanation
from llm import LLMProvider, LLMRequest, LLMUnavailable
from resume_parser import skill_in_text

# The only thing the model authors. Numbers are deliberately absent — those
# stay with the deterministic explanation.
NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string",
                    "description": "2-3 sentences on why the fit is strong or "
                                   "weak, grounded in the given facts."},
        "strengths": {"type": "array", "items": {"type": "string"},
                      "description": "What the candidate brings that this job "
                                     "values. Draw only from matched_skills."},
        "weaknesses": {"type": "array", "items": {"type": "string"},
                       "description": "Where the candidate falls short for "
                                      "this job. Draw only from missing_skills."},
        "advice": {"type": "string",
                   "description": "One paragraph of practical career advice "
                                  "for pursuing roles like this."},
        "improvements": {"type": "array", "items": {"type": "string"},
                         "description": "Prioritised, concrete next steps. "
                                        "Never list a skill from "
                                        "matched_skills here."},
    },
    "required": ["summary", "strengths", "weaknesses", "advice",
                 "improvements"],
}

_SYSTEM = (
    "You explain job-fit scores to a job seeker in the Philippines. You are "
    "given FACTS that were computed deterministically: the match percentage, "
    "the skills from the candidate's resume that this job asks for "
    "(matched_skills), and the skills this job asks for that the resume does "
    "not show (missing_skills). These facts are authoritative. Do not "
    "recompute the score, do not move a skill between matched and missing, and "
    "never tell the candidate to learn or add a skill that appears in "
    "matched_skills — they already have it. Be specific, warm, and honest; if "
    "the fit is weak, say so plainly. Keep it concise."
)


@dataclass
class AIExplanation:
    """The deterministic explanation, optionally enriched with a narrative."""
    base: ScoreExplanation
    summary: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    advice: str = ""
    improvements: list[str] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _facts(base: ScoreExplanation) -> dict:
    """The compact, authoritative block the model must not contradict."""
    return {
        "match_percent": base.score_percent,
        "matched_skills": base.title_matches + base.body_matches,
        "missing_skills": base.missing,
    }


def _build_request(base: ScoreExplanation, job: dict, effort: str) -> LLMRequest:
    description = (job.get("description") or job.get("teaser") or "")[:4000]
    prompt = (
        f"JOB TITLE: {job.get('title', '')}\n"
        f"COMPANY: {job.get('company') or 'not stated'}\n\n"
        f"FACTS (authoritative):\n{json.dumps(_facts(base), indent=2)}\n\n"
        f"JOB DESCRIPTION:\n{description}\n\n"
        "Write the explanation as the schema requires."
    )
    return LLMRequest(
        system=_SYSTEM, prompt=prompt, schema=NARRATIVE_SCHEMA,
        max_tokens=1500, effort=effort,
        # Same facts and job → same narrative, so cache on the job key.
        cache_salt=(base.job_key,))


def _reject_contradictions(base: ScoreExplanation, narrative: dict) -> None:
    """
    Guards against the model telling the candidate to learn what they have.
    The improvements list is "things to acquire"; a matched skill appearing
    there contradicts the ground truth, so the whole narrative is discarded.
    """
    matched = base.title_matches + base.body_matches
    for suggestion in narrative.get("improvements", []):
        text = suggestion.lower()
        clash = next((skill for skill in matched
                      if skill_in_text(skill, text)), None)
        if clash:
            raise LLMUnavailable(
                f"Narrative told the candidate to improve {clash!r}, which the "
                "resume already evidences — discarding it as unreliable.")


# ======================================================
# PUBLIC API
# ======================================================
def enrich(job: dict, resume_skills: list[str], resume_text: str,
           provider: LLMProvider, effort: str = "high") -> AIExplanation:
    """
    Returns the deterministic explanation, enriched with an AI narrative when
    a provider is available and its reply is trustworthy. Never raises: any
    failure leaves the deterministic explanation intact and is logged.
    """
    base = explain.explain_job(job, resume_skills, resume_text)
    result = AIExplanation(base=base)

    if not provider.is_available():
        return result

    try:
        response = provider.complete(_build_request(base, job, effort))
        _reject_contradictions(base, response.data)
    except LLMUnavailable as error:
        logging.info("Showing the deterministic explanation only: %s", error)
        return result

    narrative = response.data
    result.summary = narrative.get("summary", "")
    result.strengths = narrative.get("strengths", [])
    result.weaknesses = narrative.get("weaknesses", [])
    result.advice = narrative.get("advice", "")
    result.improvements = narrative.get("improvements", [])
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    return result

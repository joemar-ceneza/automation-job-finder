"""
ai_learning.py
AI mode for learning recommendations. Standard mode decides *what* to learn and
in what order; AI mode writes the prose around it — a roadmap, a week-by-week
plan, and project ideas that combine a skill you're learning with ones you
already have.

One rule is enforced in code rather than trusted to the prompt: the model must
not supply links. A model asked for course URLs produces plausible ones that
404, so the curated links in learning_map are the only ones the user ever sees.
Any list item carrying a URL is dropped, and a roadmap carrying one is refused
outright — a study plan pointing at a dead link is worse than no plan.

Consumes the computed plan rather than re-deriving it, so it never reorders your
prerequisites or invents a demand figure, and degrades to the deterministic plan
on any failure.
"""
import json
import logging
import re
from dataclasses import dataclass, field

from learning import LearningPlan
from llm import LLMProvider, LLMRequest, LLMUnavailable

# Anything that looks like a link the model made up.
_URL = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)

ROADMAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "roadmap": {"type": "string",
                    "description": "2-4 sentences on how to approach this "
                                   "study plan and why this order."},
        "weekly_plan": {"type": "array", "items": {"type": "string"},
                        "description": "One line per week, e.g. 'Week 1: "
                                       "Docker basics — images, containers, "
                                       "compose.'"},
        "projects": {"type": "array", "items": {"type": "string"},
                     "description": "Project ideas that combine a skill being "
                                    "learned with skills the candidate already "
                                    "has."},
    },
    "required": ["roadmap", "weekly_plan", "projects"],
}

_SYSTEM = (
    "You are a study coach for a developer in the Philippines. You are given an "
    "ordered learning plan that was computed for them — the skills, the order, "
    "the hours — plus the skills they already have. Write the narrative around "
    "that plan: why this order makes sense, a realistic week-by-week schedule "
    "that respects the hours given, and project ideas that pair a new skill "
    "with one they already know. Do NOT include any links or URLs — course "
    "links are supplied separately and yours would be wrong. Do not reorder the "
    "plan or invent skills that are not in it. Be concrete and encouraging."
)


@dataclass
class AILearning:
    """The deterministic plan, optionally with an AI roadmap around it."""
    base: LearningPlan
    roadmap: str = ""
    weekly_plan: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False
    note: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _facts(base: LearningPlan, resume_skills: list[str]) -> dict:
    return {
        "already_known": resume_skills[:25],
        "total_hours": base.total_hours,
        "plan": [
            {"order": index, "skill": step.skill, "hours": step.hours,
             "difficulty": step.difficulty,
             "jobs_asking": step.demand,
             "foundation_for": step.unlocks}
            for index, step in enumerate(base.steps, 1)
        ],
    }


def _build_request(base: LearningPlan, resume_skills: list[str],
                   effort: str) -> LLMRequest:
    prompt = (
        f"THE COMPUTED PLAN (authoritative — do not reorder or extend):\n"
        f"{json.dumps(_facts(base, resume_skills), indent=2)}\n\n"
        "Write the roadmap as the schema requires. No links."
    )
    salt = tuple(step.skill for step in base.steps)
    return LLMRequest(system=_SYSTEM, prompt=prompt, schema=ROADMAP_SCHEMA,
                      max_tokens=1500, effort=effort, cache_salt=salt)


def _without_links(items: list[str]) -> list[str]:
    """Drops any item carrying a URL — curated links are the only ones shown."""
    return [item.strip() for item in items
            if item and item.strip() and not _URL.search(item)]


# ======================================================
# PUBLIC API
# ======================================================
def enrich(base: LearningPlan, resume_skills: list[str],
           provider: LLMProvider, effort: str = "high") -> AILearning:
    """
    Returns the deterministic plan, enriched with an AI roadmap when a provider
    is available and the reply carries no invented links. Never raises: any
    failure leaves the computed plan intact and records why.
    """
    result = AILearning(base=base)
    if not base.steps:
        result.note = "there is nothing to plan yet"
        return result
    if not provider.is_available():
        return result

    try:
        response = provider.complete(
            _build_request(base, resume_skills, effort))
    except LLMUnavailable as error:
        logging.info("Showing the computed learning plan only: %s", error)
        result.note = str(error)
        return result

    data = response.data
    roadmap = (data.get("roadmap") or "").strip()
    if not roadmap:
        result.note = "the model returned an empty roadmap"
        return result
    if _URL.search(roadmap):
        # The one thing the model must not do. Curated links only.
        logging.warning("Discarding an AI roadmap that invented a link — the "
                        "curated resources stand.")
        result.note = "the model tried to invent course links"
        return result

    result.roadmap = roadmap
    result.weekly_plan = _without_links(data.get("weekly_plan", []))
    result.projects = _without_links(data.get("projects", []))
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    logging.info("Wrote a study roadmap with AI (%s%s).", response.model,
                 ", cached" if response.from_cache else "")
    return result

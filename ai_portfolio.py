"""
ai_portfolio.py
AI mode for portfolio matching. Standard mode ranks your projects against a job;
AI mode writes the pitch — why this project fits this role, and which part of it
to lead with in the application.

The risk here is specific and worth guarding in code: a pitch that credits a
project with a technology it never used is a lie you would repeat in an
interview. So each pitch is checked with the resume rewriter's fabrication
verifier, against the project's own text — its tags, summary and highlights. A
pitch claiming something the project does not evidence is dropped and that
project keeps its deterministic match instead.

That check is deliberately conservative, and on a short project blurb it will
occasionally drop an honest pitch — a summary reading "Scrapes job ads" does not
evidence the skill "Web Scraping" as far as the alias map is concerned. The
trade is the right way round: the cost of a false positive is one missing pitch
while the ranking stands, and the cost of a false negative is a claim you would
have to defend in an interview. Listing a technology explicitly in the
project's `tech` tags is what makes it safe to mention.

Consumes the computed ranking rather than re-ranking, so the order stays the
scorer's, and degrades to the plain match on any failure.
"""
import json
import logging
from dataclasses import dataclass, field

import ai_rewrite
from llm import LLMProvider, LLMRequest, LLMUnavailable
from portfolio import PortfolioMatch, ProjectMatch

# How many projects to pitch. Beyond this an application would not mention them.
_MAX_PITCHES = 3

PITCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pitches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "why_it_fits": {
                        "type": "string",
                        "description": "1-2 sentences on why this project is "
                                       "relevant to this job."},
                    "lead_with": {
                        "type": "string",
                        "description": "The single aspect of the project to "
                                       "lead with in the application."},
                },
                "required": ["why_it_fits", "lead_with"],
            },
            "description": "One pitch per project given, in the same order.",
        }
    },
    "required": ["pitches"],
}

_SYSTEM = (
    "You help a developer in the Philippines decide which of their projects to "
    "show for a job. For each project you are given — its technologies, summary "
    "and highlights — write why it fits this specific role and the one aspect "
    "to lead with. Use ONLY what each project actually states: never credit a "
    "project with a technology, a metric, or a feature it does not list. Return "
    "exactly one pitch per project, in the same order. Be concrete and brief."
)


@dataclass
class ProjectPitch:
    """A ranked project with its AI-written pitch."""
    match: ProjectMatch
    why_it_fits: str = ""
    lead_with: str = ""


@dataclass
class AIPortfolio:
    """The deterministic ranking, optionally with pitches attached."""
    base: PortfolioMatch
    pitches: list[ProjectPitch] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False
    note: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _project_text(match: ProjectMatch) -> str:
    """Everything a project actually claims — the ground truth for a pitch."""
    project = match.project
    return " ".join([project.name, project.summary, " ".join(project.tech),
                     " ".join(project.highlights)])


def _build_request(job: dict, top: list[ProjectMatch],
                   effort: str) -> LLMRequest:
    projects = [
        {"name": match.project.name,
         "technologies": match.project.tech,
         "summary": match.project.summary,
         "highlights": match.project.highlights,
         "job_wants_these_of_its_tech": match.matched}
        for match in top
    ]
    description = (job.get("description") or job.get("teaser") or "")[:2500]
    prompt = (
        f"JOB: {job.get('title', '')} at {job.get('company') or 'the company'}\n\n"
        f"ADVERTISEMENT:\n{description}\n\n"
        f"MY PROJECTS (use only what these state):\n"
        f"{json.dumps(projects, indent=2)}\n\n"
        f"Write one pitch for each of the {len(top)} projects, in order."
    )
    return LLMRequest(
        system=_SYSTEM, prompt=prompt, schema=PITCH_SCHEMA,
        max_tokens=1200, effort=effort,
        cache_salt=(job.get("job_key", ""),
                    *(match.project.name for match in top)))


def _allowed_numbers(job: dict) -> str:
    """A company or title containing digits is legitimate, not a made-up metric."""
    return f"{job.get('company') or ''} {job.get('title') or ''}"


# ======================================================
# PUBLIC API
# ======================================================
def enrich(job: dict, base: PortfolioMatch, provider: LLMProvider,
           effort: str = "high") -> AIPortfolio:
    """
    Returns the deterministic ranking, with an AI pitch attached to the top
    projects when a provider is available and the pitch is grounded. Never
    raises; a pitch that credits a project with something it does not state is
    dropped rather than shown.
    """
    result = AIPortfolio(base=base)
    if not base.matches:
        result.note = "there are no projects to pitch"
        return result
    if not provider.is_available():
        return result

    top = base.matches[:_MAX_PITCHES]
    try:
        response = provider.complete(_build_request(job, top, effort))
    except LLMUnavailable as error:
        logging.info("Showing the deterministic portfolio match only: %s",
                     error)
        result.note = str(error)
        return result

    pitches = response.data.get("pitches", [])
    if len(pitches) != len(top):
        logging.warning("Model returned %d pitches for %d projects — showing "
                        "the ranking only.", len(pitches), len(top))
        result.note = "the model's pitches did not line up with the projects"
        return result

    allowed = _allowed_numbers(job)
    kept = []
    for match, pitch in zip(top, pitches):
        why = (pitch.get("why_it_fits") or "").strip()
        lead = (pitch.get("lead_with") or "").strip()
        if not why:
            continue
        source = _project_text(match)
        reason = next(
            (found for found in
             (ai_rewrite.verify_no_fabrication(text, source,
                                               allowed_number_context=allowed)
              for text in (why, lead) if text)
             if found), None)
        if reason:
            logging.info("Dropped a pitch for %s that %s.",
                         match.project.name, reason)
            continue
        kept.append(ProjectPitch(match=match, why_it_fits=why, lead_with=lead))

    if not kept:
        result.note = "every pitch credited a project with something it does " \
                      "not state"
        return result

    result.pitches = kept
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    logging.info("Pitched %d of %d projects with AI (%s%s).", len(kept),
                 len(top), response.model,
                 ", cached" if response.from_cache else "")
    return result

"""
ai_company.py
AI mode for company intelligence. Standard mode reports what your database
knows; AI mode reads what the employer's own advertisements imply about how they
work — culture, hiring process, what looks good and what looks concerning.

The risk here is unusual and worth naming. Asked about a real company, a model
will happily supply what it "knows" — headcount, funding, founding year,
reputation — from training data that is unsourced, possibly stale, and
indistinguishable in tone from the parts it actually read. That is precisely the
company intelligence this project decided not to ship. So the reading is
verified against the advertisement text with the resume rewriter's verifier: a
claim carrying a figure the adverts never mention is dropped, and everything
that survives is labelled as inference from the ad rather than researched fact.

Consumes the deterministic profile, never restates its numbers, and degrades to
that profile on any failure.
"""
import json
import logging
from dataclasses import dataclass, field

import ai_rewrite
from company import CompanyProfile
from llm import LLMProvider, LLMRequest, LLMUnavailable

# How many of a company's adverts to read. Beyond this the prompt bloats without
# telling you much more about how they write.
_MAX_ADVERTS = 3
_ADVERT_CHARS = 1800

COMPANY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "culture": {"type": "string",
                    "description": "What the adverts' own language suggests "
                                   "about how this employer works."},
        "interview_process": {"type": "string",
                              "description": "What the adverts say or imply "
                                             "about hiring. Say plainly if "
                                             "they say nothing."},
        "advantages": {"type": "array", "items": {"type": "string"},
                       "description": "What looks good, from the adverts."},
        "concerns": {"type": "array", "items": {"type": "string"},
                     "description": "What looks concerning, from the adverts."},
    },
    "required": ["culture", "interview_process", "advantages", "concerns"],
}

_SYSTEM = (
    "You read job advertisements to infer how an employer works. You are given "
    "that company's own adverts. Base EVERYTHING on those adverts and say so — "
    "you are inferring from how they write, not reporting researched fact. "
    "Never state a company's size, headcount, funding, founding year, revenue, "
    "or reputation: you have not verified any of it and a wrong figure here is "
    "worse than silence. If the adverts do not say something, say they do not. "
    "Be brief and specific about what the wording actually shows."
)


@dataclass
class AICompany:
    """The deterministic profile, optionally with an inferred reading."""
    base: CompanyProfile
    culture: str = ""
    interview_process: str = ""
    advantages: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False
    note: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _advert_text(postings: list[dict]) -> str:
    """The company's own words — the only thing a reading may be based on."""
    parts = []
    for job in postings[:_MAX_ADVERTS]:
        body = (job.get("description") or job.get("teaser") or "")[:_ADVERT_CHARS]
        parts.append(f"{job.get('title', '')}\n{body}")
    return "\n\n---\n\n".join(parts)


def _build_request(profile: CompanyProfile, postings: list[dict],
                   effort: str) -> LLMRequest:
    prompt = (
        f"COMPANY: {profile.name}\n"
        f"POSTINGS TRACKED: {profile.postings}\n"
        f"ROLES THEY REPEAT: "
        f"{json.dumps(dict(profile.repeated_roles))}\n\n"
        f"THEIR ADVERTISEMENTS (your only source):\n"
        f"{_advert_text(postings)}\n\n"
        "Write the reading as the schema requires. Infer only from the text "
        "above."
    )
    return LLMRequest(system=_SYSTEM, prompt=prompt, schema=COMPANY_SCHEMA,
                      max_tokens=1200, effort=effort,
                      cache_salt=(profile.name, str(profile.postings)))


def _grounded(text: str, source: str, allowed: str) -> bool:
    """True when a claim invents no figure or skill the adverts lack."""
    return ai_rewrite.verify_no_fabrication(
        text, source, allowed_number_context=allowed) is None


# ======================================================
# PUBLIC API
# ======================================================
def enrich(profile: CompanyProfile, postings: list[dict],
           provider: LLMProvider, effort: str = "high") -> AICompany:
    """
    Returns the deterministic profile, enriched with a reading inferred from the
    company's own adverts. Never raises; a claim carrying a figure the adverts
    never mention is dropped, and a fabricated culture summary refuses the whole
    reading rather than presenting invented facts as inference.
    """
    result = AICompany(base=profile)
    if not postings:
        result.note = "there are no adverts to read"
        return result
    if not provider.is_available():
        return result

    try:
        response = provider.complete(
            _build_request(profile, postings, effort))
    except LLMUnavailable as error:
        logging.info("Showing the company profile only: %s", error)
        result.note = str(error)
        return result

    data = response.data
    culture = (data.get("culture") or "").strip()
    if not culture:
        result.note = "the model returned an empty reading"
        return result

    source = _advert_text(postings)
    allowed = f"{profile.name} " + " ".join(
        title for title, _count in profile.repeated_roles)

    if not _grounded(culture, source, allowed):
        logging.warning("Discarding an AI company reading that stated a figure "
                        "the adverts never mention.")
        result.note = ("the model asserted details the adverts do not contain "
                       "— most likely half-remembered rather than read")
        return result

    process = (data.get("interview_process") or "").strip()
    result.culture = culture
    result.interview_process = process if _grounded(process, source,
                                                    allowed) else ""
    result.advantages = [item.strip() for item in data.get("advantages", [])
                         if item and item.strip()
                         and _grounded(item, source, allowed)]
    result.concerns = [item.strip() for item in data.get("concerns", [])
                       if item and item.strip()
                       and _grounded(item, source, allowed)]
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    logging.info("Read %s's adverts with AI (%s%s).", profile.name,
                 response.model, ", cached" if response.from_cache else "")
    return result

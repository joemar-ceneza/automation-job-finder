"""
ai_summary.py
AI mode for the job summary. Standard mode extracts the sections and catches the
obvious red flags; AI mode adds what rules cannot — a plain-English read of the
role, its pros and cons as advertised, a career-growth take, and the subtler
warning signs (vague scope, three jobs bundled into one posting, churn signals).

Like the rest of the AI layer it consumes Standard mode's output rather than
re-deriving facts: the model is handed the already-extracted sections and asked
to interpret the advertisement, not to invent details of it. It runs per job
through whatever provider is configured — including a fully local Ollama model —
and degrades to the deterministic summary on any failure. There is no batch
path here; that is a cloud cost optimisation, and this must work offline too.
"""
import json
import logging
from dataclasses import dataclass, field

from llm import LLMProvider, LLMRequest, LLMUnavailable
from summary import JobSummary

SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string",
                     "description": "2-3 plain-English sentences on what this "
                                    "role is and who it suits."},
        "pros": {"type": "array", "items": {"type": "string"},
                 "description": "What is appealing about the role as "
                                "advertised."},
        "cons": {"type": "array", "items": {"type": "string"},
                 "description": "Drawbacks or concerns evident from the "
                                "advertisement."},
        "growth": {"type": "string",
                   "description": "One sentence on the career-growth potential "
                                  "this role offers."},
        "red_flags": {"type": "array", "items": {"type": "string"},
                      "description": "Subtler warning signs — vague scope, "
                                     "several jobs bundled into one, churn "
                                     "signals. Do not repeat obvious scams."},
    },
    "required": ["overview", "pros", "cons", "growth", "red_flags"],
}

_SYSTEM = (
    "You summarise job advertisements for a job seeker in the Philippines. Base "
    "everything ONLY on the advertisement text you are given — do not invent a "
    "salary, a company fact, or a requirement it does not state. The overview "
    "and pros/cons are your read of the role exactly as advertised. For "
    "red_flags, name only subtler concerns a careful reader would spot (vague "
    "or ballooning scope, several distinct jobs in one posting, signs of high "
    "turnover); the obvious scams are already handled, so do not repeat them. "
    "Be concise and honest."
)


@dataclass
class AISummary:
    """The deterministic summary, optionally enriched with an AI reading."""
    base: JobSummary
    overview: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    growth: str = ""
    red_flags: list[str] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False
    note: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _facts(job: dict, base: JobSummary) -> dict:
    """The already-extracted facts, so the model interprets rather than re-parses."""
    return {
        "title": base.title,
        "company": base.company or "not stated",
        "work_arrangement": base.work_arrangement,
        "salary": base.salary_text or "not stated",
        "required_years": base.required_years,
        "responsibilities": base.responsibilities,
        "requirements": base.requirements,
        "nice_to_have": base.nice_to_have,
        "benefits": base.benefits,
    }


def _build_request(job: dict, base: JobSummary, effort: str) -> LLMRequest:
    description = (job.get("description") or job.get("teaser") or "")[:4000]
    prompt = (
        f"EXTRACTED FACTS (already parsed — build on these):\n"
        f"{json.dumps(_facts(job, base), indent=2)}\n\n"
        f"FULL ADVERTISEMENT:\n{description}\n\n"
        "Write the summary as the schema requires."
    )
    return LLMRequest(
        system=_SYSTEM, prompt=prompt, schema=SUMMARY_SCHEMA,
        max_tokens=1500, effort=effort,
        # Same advert → same summary, so cache on the job key.
        cache_salt=(base.job_key,))


# ======================================================
# PUBLIC API
# ======================================================
def enrich(job: dict, base: JobSummary, provider: LLMProvider,
           effort: str = "high") -> AISummary:
    """
    Returns the deterministic summary, enriched with an AI reading when a
    provider is available and the reply is usable. Never raises: any failure
    leaves the Standard summary intact and records why in `note`.
    """
    result = AISummary(base=base)
    if not provider.is_available():
        return result

    try:
        response = provider.complete(_build_request(job, base, effort))
    except LLMUnavailable as error:
        logging.info("Showing the deterministic summary only: %s", error)
        result.note = str(error)
        return result

    data = response.data
    overview = (data.get("overview") or "").strip()
    if not overview:
        result.note = "the model returned an empty summary"
        return result

    result.overview = overview
    result.pros = data.get("pros", [])
    result.cons = data.get("cons", [])
    result.growth = (data.get("growth") or "").strip()
    # Don't repeat a warning the deterministic rules already caught.
    already = {flag.lower() for flag in base.red_flags}
    result.red_flags = [flag for flag in data.get("red_flags", [])
                        if flag.strip() and flag.lower() not in already]
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    logging.info("Summarised %s with AI (%s%s).", base.title, response.model,
                 ", cached" if response.from_cache else "")
    return result

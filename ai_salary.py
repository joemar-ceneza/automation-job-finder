"""
ai_salary.py
AI mode for salary analytics. Standard mode does the arithmetic — the band, the
percentiles, the yearly and hourly figures; AI mode reads them: how competitive
the pay is, how to frame a negotiation, and how the number sits against the
seniority the advert asks for.

It consumes the computed assessment rather than the raw advert, so it never
invents a market figure — the band and the corpus statistics are handed to it,
and it explains them. It runs per job through whatever provider is configured
(local Ollama included) and falls back to the deterministic band on any failure,
recording why.
"""
import json
import logging
from dataclasses import dataclass

from llm import LLMProvider, LLMRequest, LLMUnavailable
from salary_bands import SalaryAssessment

SALARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "competitiveness": {"type": "string",
                            "description": "2-3 sentences on how competitive "
                                           "this pay is, grounded in the band "
                                           "and corpus figures given."},
        "negotiation": {"type": "string",
                        "description": "How to frame a negotiation — a target "
                                       "range and the leverage to justify it."},
        "seniority_read": {"type": "string",
                           "description": "How the pay reads against the "
                                          "seniority/years the advert asks "
                                          "for."},
    },
    "required": ["competitiveness", "negotiation", "seniority_read"],
}

_SYSTEM = (
    "You advise a job seeker in the Philippines on pay. You are given a "
    "deterministic salary assessment — the advertised monthly figure, its band "
    "(Below / Competitive / Above) versus the roles they track, and the corpus "
    "median and quartiles. Ground everything in those numbers; never invent a "
    "market figure or claim a benchmark beyond the sample you are given. Give a "
    "clear competitiveness read, a concrete negotiation framing (a target range "
    "and the leverage for it), and how the pay sits against the seniority the "
    "advert asks for. Be concise and honest — if the pay is weak, say so."
)


@dataclass
class AISalary:
    """The deterministic assessment, optionally with an AI reading."""
    base: SalaryAssessment
    competitiveness: str = ""
    negotiation: str = ""
    seniority_read: str = ""
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False
    note: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _facts(job: dict, base: SalaryAssessment) -> dict:
    return {
        "role": base.role,
        "required_years": job.get("required_years") or "not stated",
        "monthly": base.monthly,
        "yearly": base.yearly,
        "band": base.band or "not enough data to band",
        "corpus_sample": base.sample_size,
        "corpus_median": base.corpus_median,
        "corpus_p25": base.p25,
        "corpus_p75": base.p75,
    }


def _build_request(job: dict, base: SalaryAssessment, effort: str) -> LLMRequest:
    description = (job.get("description") or job.get("teaser") or "")[:2500]
    prompt = (
        f"JOB: {job.get('title', '')}\n\n"
        f"SALARY ASSESSMENT (authoritative — build on these):\n"
        f"{json.dumps(_facts(job, base), indent=2)}\n\n"
        f"ADVERTISEMENT:\n{description}\n\n"
        "Write the reading as the schema requires."
    )
    return LLMRequest(
        system=_SYSTEM, prompt=prompt, schema=SALARY_SCHEMA,
        max_tokens=1200, effort=effort, cache_salt=(job.get("job_key", ""),))


# ======================================================
# PUBLIC API
# ======================================================
def enrich(job: dict, base: SalaryAssessment, provider: LLMProvider,
           effort: str = "high") -> AISalary:
    """
    Returns the deterministic assessment, enriched with an AI reading when a
    provider is available and the reply is usable. Never raises; a job with no
    stated salary or any failure leaves the assessment intact and records why.
    """
    result = AISalary(base=base)
    if not base.has_salary:
        result.note = "the advert states no salary to read"
        return result
    if not provider.is_available():
        return result

    try:
        response = provider.complete(_build_request(job, base, effort))
    except LLMUnavailable as error:
        logging.info("Showing the deterministic salary band only: %s", error)
        result.note = str(error)
        return result

    data = response.data
    competitiveness = (data.get("competitiveness") or "").strip()
    if not competitiveness:
        result.note = "the model returned an empty reading"
        return result

    result.competitiveness = competitiveness
    result.negotiation = (data.get("negotiation") or "").strip()
    result.seniority_read = (data.get("seniority_read") or "").strip()
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    logging.info("Read the salary for %s with AI (%s%s).",
                 job.get("title", ""), response.model,
                 ", cached" if response.from_cache else "")
    return result

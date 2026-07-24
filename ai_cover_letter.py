"""
ai_cover_letter.py
AI mode for the cover letter: instead of filling a template, the model writes
the body prose from the candidate's real accomplishments — then every paragraph
is run through the same fabrication verifier the resume rewriter uses.

The design is the one the whole AI layer follows: Standard mode selects the
truth (which skills the advert wants and the resume evidences), and the model
is handed those facts plus the resume's actual bullets to write from. It is
never given an invented number or skill to work with, and any paragraph that
introduces one anyway is caught in code — at which point the whole letter is
discarded and the deterministic template returned instead. A letter you send an
employer is not a place to trust the prompt alone.

Only the body paragraphs are the model's. The date, address block, salutation,
and signature are structural and stay deterministic — they are copied from the
Standard letter, which is also the fallback if AI mode cannot be trusted.
"""
import logging

import ai_rewrite
import cover_letter
from cover_letter import CoverLetter
from llm import LLMProvider, LLMRequest, LLMUnavailable
from resume_model import MasterResume

BODY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3 to 4 body paragraphs, each a few sentences. The "
                           "letter body only — no date, no salutation, no "
                           "sign-off.",
        }
    },
    "required": ["paragraphs"],
}

_SYSTEM = (
    "You write the body of a cover letter for a job seeker in the Philippines. "
    "You are given the candidate's real accomplishments and the skills this "
    "job asks for that their resume already evidences. Write warm, specific, "
    "confident prose that connects those accomplishments to what the job "
    "wants. Strict rules: use ONLY the experience, skills, and figures you are "
    "given — never introduce an employer, a metric, a number, or a technology "
    "the candidate has not shown, and do not inflate numbers. Write the body "
    "only: no date, no address, no 'Dear ...', no 'Sincerely'. Three to four "
    "short paragraphs, opening with genuine interest in this specific role."
)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _allowed_number_context(job: dict) -> str:
    """
    Numbers that are legitimate in the letter but not from the resume — the
    company name and the job title. A "3M" or a "Web 2.0" in the role should
    not be mistaken for an invented metric.
    """
    return f"{job.get('company') or ''} {job.get('title') or ''}"


def _build_request(resume: MasterResume, job: dict, standard: CoverLetter,
                   effort: str) -> LLMRequest:
    bullets = "\n".join(f"- {bullet}" for bullet in resume.all_bullets())
    wanted = (cover_letter._readable_list(standard.skills_used)
              or "the skills the advert lists")
    description = (job.get("description") or job.get("teaser") or "")[:3000]
    prompt = (
        f"CANDIDATE: {resume.contact.name}, "
        f"{cover_letter._headline(resume.contact)}\n"
        f"TARGET JOB: {job.get('title', '')} at "
        f"{job.get('company') or 'the company'}\n"
        f"SKILLS TO EMPHASISE (the advert wants these and the resume shows "
        f"them): {wanted}\n\n"
        f"THE CANDIDATE'S REAL ACCOMPLISHMENTS — draw only from these:\n"
        f"{bullets}\n\n"
        f"JOB DESCRIPTION:\n{description}\n\n"
        "Write the cover letter body as the schema requires."
    )
    return LLMRequest(
        system=_SYSTEM, prompt=prompt, schema=BODY_SCHEMA,
        max_tokens=1200, effort=effort,
        # Same resume and job → same letter, so cache on the job key.
        cache_salt=(job.get("job_key", ""),))


# ======================================================
# PUBLIC API
# ======================================================
def compose(resume: MasterResume, job: dict, provider: LLMProvider,
            tone: str = "direct", recipient: str | None = None,
            effort: str = "high") -> CoverLetter:
    """
    Returns a cover letter whose body the model has written, grounded in the
    resume and checked against it. Never raises and never fabricates: without a
    provider, on any failure, or if a single paragraph invents a figure or a
    skill, the deterministic template letter is returned unchanged.
    """
    standard = cover_letter.compose(resume, job, tone=tone, recipient=recipient)
    if not provider.is_available():
        return standard

    try:
        response = provider.complete(
            _build_request(resume, job, standard, effort))
    except LLMUnavailable as error:
        logging.info("Using the template cover letter — AI unavailable: %s",
                     error)
        return standard

    paragraphs = [para.strip()
                  for para in response.data.get("paragraphs", [])
                  if para and para.strip()]
    if not paragraphs:
        logging.warning("The model returned no cover-letter body — using the "
                        "template letter.")
        return standard

    resume_text = resume.full_text()
    allowed = _allowed_number_context(job)
    for para in paragraphs:
        reason = ai_rewrite.verify_no_fabrication(
            para, resume_text, allowed_number_context=allowed)
        if reason:
            logging.warning("Discarding the AI cover letter — a paragraph %s. "
                            "Using the template letter instead.", reason)
            return standard

    logging.info("Wrote a cover letter for %s at %s with AI (%s%s).",
                 standard.position, standard.company or "unknown",
                 response.model, ", cached" if response.from_cache else "")
    return CoverLetter(
        company=standard.company,
        position=standard.position,
        recipient=standard.recipient,
        sender=standard.sender,
        paragraphs=paragraphs,
        tone=tone,
        letter_date=standard.letter_date,
        skills_used=standard.skills_used,
        model=response.model,
        from_cache=response.from_cache,
        ai_used=True,
    )

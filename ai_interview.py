"""
ai_interview.py
AI mode for interview preparation: the Standard sheet gives you the questions
and a talking point for each; AI mode drafts a full suggested answer for every
question, written in the first person from your real accomplishments — then each
answer is run through the same fabrication verifier the resume rewriter uses.

The pattern is the one the whole AI layer follows: Standard mode computes the
sheet (which skills matched, which are gaps, the likely questions), and the
model is handed those questions plus the resume's actual bullets to answer from.
It is never given an invented skill or number to work with, and any answer that
introduces one is dropped in code — that question keeps its deterministic
talking point instead. A practised answer that quietly claims experience you do
not have is worse than no answer, so the verifier, not the prompt, is what makes
this safe to rehearse from.
"""
import logging
from dataclasses import dataclass, field

import ai_rewrite
from interview import InterviewPrep
from llm import LLMProvider, LLMRequest, LLMUnavailable
from resume_model import MasterResume

ANSWERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One suggested answer per question, in the same "
                           "order as the questions given.",
        }
    },
    "required": ["answers"],
}

_SYSTEM = (
    "You are an interview coach for a job seeker in the Philippines. For each "
    "question you are given, write a concise, first-person suggested answer — "
    "two to four sentences — that the candidate could rehearse. Ground every "
    "answer ONLY in the real accomplishments you are given: never claim an "
    "employer, a skill, a technology, or a number the candidate has not shown, "
    "and do not inflate figures. For a question about a skill the candidate "
    "lacks, be honest that they haven't used it and pivot to the closest real "
    "experience they do have. Return exactly one answer per question, in the "
    "same order."
)


@dataclass
class AIAnswer:
    """A question paired with its AI-drafted, resume-grounded answer."""
    prompt: str
    answer: str


@dataclass
class AIInterviewPrep:
    """The deterministic sheet, optionally with a drafted answer per question."""
    base: InterviewPrep
    answers: list[AIAnswer] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False
    note: str = ""


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _build_request(resume: MasterResume, job: dict, prep: InterviewPrep,
                   effort: str) -> LLMRequest:
    bullets = "\n".join(f"- {bullet}" for bullet in resume.all_bullets())
    numbered = "\n".join(f"{index}. {question.prompt}"
                         for index, question in enumerate(prep.questions, 1))
    prompt = (
        f"CANDIDATE: {resume.contact.name}\n"
        f"TARGET JOB: {prep.position} at {prep.company or 'the company'}\n\n"
        f"THE CANDIDATE'S REAL ACCOMPLISHMENTS — answer only from these:\n"
        f"{bullets}\n\n"
        f"Write a suggested answer for each of these {len(prep.questions)} "
        f"questions:\n{numbered}"
    )
    return LLMRequest(
        system=_SYSTEM, prompt=prompt, schema=ANSWERS_SCHEMA,
        max_tokens=2000, effort=effort,
        cache_salt=(prep.job_key,))


def _allowed_number_context(job: dict) -> str:
    """Company and title numbers are legitimate, not invented metrics."""
    return f"{job.get('company') or ''} {job.get('title') or ''}"


# ======================================================
# PUBLIC API
# ======================================================
def enrich(resume: MasterResume, job: dict, prep: InterviewPrep,
           provider: LLMProvider, effort: str = "high") -> AIInterviewPrep:
    """
    Returns the deterministic sheet, enriched with a drafted answer per question
    when a provider is available and the answer is grounded. Never raises: no
    provider or any failure leaves the Standard sheet intact, and a single
    fabricated answer is dropped (its question keeps the deterministic hint)
    rather than discarding the whole set.
    """
    result = AIInterviewPrep(base=prep)
    if not prep.questions or not provider.is_available():
        return result

    try:
        response = provider.complete(_build_request(resume, job, prep, effort))
    except LLMUnavailable as error:
        logging.info("Showing the deterministic interview sheet only: %s",
                     error)
        result.note = str(error)
        return result

    answers = response.data.get("answers", [])
    if len(answers) != len(prep.questions):
        logging.warning("Model returned %d answers for %d questions — showing "
                        "the deterministic sheet only.", len(answers),
                        len(prep.questions))
        result.note = "the model's answers did not line up with the questions"
        return result

    resume_text = resume.full_text()
    allowed = _allowed_number_context(job)
    kept = []
    for question, answer in zip(prep.questions, answers):
        answer = (answer or "").strip()
        if not answer:
            continue
        reason = ai_rewrite.verify_no_fabrication(
            answer, resume_text, allowed_number_context=allowed)
        if reason:
            logging.info("Dropped a drafted answer that %s.", reason)
            continue
        kept.append(AIAnswer(prompt=question.prompt, answer=answer))

    if not kept:
        result.note = "every drafted answer claimed something not in your resume"
        return result

    result.answers = kept
    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True
    logging.info("Drafted %d of %d interview answers with AI (%s%s).",
                 len(kept), len(prep.questions), response.model,
                 ", cached" if response.from_cache else "")
    return result

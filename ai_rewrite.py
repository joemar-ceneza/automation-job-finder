"""
ai_rewrite.py
AI mode for the resume optimiser: rewrite existing bullet points for impact and
keyword alignment, without inventing anything.

"Do not fabricate" is not left to the prompt. Every rewritten bullet is checked
in code against the original resume, and a rewrite that introduces a number or a
skill the resume does not already contain is rejected — the original bullet is
kept instead. The prompt gets the model most of the way; the verifier is what
lets you trust the file you are about to send to an employer.

The rewriter operates on the structured resume, so it can only touch the text
of bullets. Employers, dates, titles, and the skills list are never sent for
rewriting and so cannot be altered.
"""
import copy
import logging
import re
from dataclasses import dataclass, field

import config
import skill_extractor
from llm import LLMProvider, LLMRequest, LLMUnavailable
from resume_model import Entry, MasterResume

# Digit runs: "30", "30%", "1,200", "9+". Compared as normalised tokens.
_NUMBER = re.compile(r"\d[\d,]*")

REWRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rewrites": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One rewritten bullet per input bullet, in the "
                           "same order.",
        }
    },
    "required": ["rewrites"],
}

_SYSTEM = (
    "You rewrite resume bullet points to be stronger and to surface the "
    "experience a specific job is looking for. Strict rules: rewrite ONLY what "
    "each bullet already says — never introduce an employer, a date, a metric "
    "or number, or a technology that is not already in the original bullet. Do "
    "not inflate numbers. If a bullet is already strong, return it nearly "
    "unchanged. Keep each to a single sentence, starting with a strong past- "
    "or present-tense verb. Return exactly as many rewrites as you are given, "
    "in the same order."
)


@dataclass
class RewriteResult:
    """The rewritten resume plus an account of what happened."""
    resume: MasterResume
    rewritten: int = 0
    kept_original: int = 0
    rejections: list[str] = field(default_factory=list)
    model: str = ""
    from_cache: bool = False
    ai_used: bool = False


# ======================================================
# THE FABRICATION VERIFIER
# ======================================================
def _numbers(text: str) -> set[str]:
    return {match.replace(",", "") for match in _NUMBER.findall(text or "")}


def _skills_in(text: str) -> set[str]:
    """
    Credible technology skills mentioned in the text. Uses the extractor's
    ambiguous-word guard, so a plain word like "automation" or "security" is
    not counted as a claimed skill unless it reads as one — otherwise the
    verifier would reject honest rewrites for using ordinary English.
    """
    return {skill for skill, _category, _in_title
            in skill_extractor.extract_skills("", text or "")}


def verify_no_fabrication(rewrite: str, resume_text: str) -> str | None:
    """
    Returns a reason string when the rewrite claims something the resume does
    not support, or None when it is clean.

    Two things are checked, because they are the two ways a rewrite invents
    experience: a number that appears nowhere in the resume (a made-up metric),
    and a known technology the resume never mentions (a claimed skill).
    """
    resume_numbers = _numbers(resume_text)
    invented_numbers = _numbers(rewrite) - resume_numbers
    if invented_numbers:
        return (f"introduces a figure not in your resume: "
                f"{', '.join(sorted(invented_numbers))}")

    invented_skills = _skills_in(rewrite) - _skills_in(resume_text)
    if invented_skills:
        return (f"claims a skill not in your resume: "
                f"{', '.join(sorted(invented_skills))}")
    return None


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _bullet_locations(resume: MasterResume) -> list[tuple[Entry, int]]:
    """Every entry bullet, as (entry, index) so a rewrite can be applied back."""
    return [(entry, index)
            for section in resume.sections
            for entry in section.entries
            for index in range(len(entry.bullets))]


def _job_skills(job: dict) -> list[str]:
    body = job.get("description") or job.get("teaser") or ""
    return [skill for skill, _c, _t
            in skill_extractor.extract_skills(job.get("title", ""), body)]


def _build_request(bullets: list[str], job: dict, effort: str) -> LLMRequest:
    wanted = ", ".join(_job_skills(job)) or "the skills in the description"
    numbered = "\n".join(f"{i}. {bullet}" for i, bullet in enumerate(bullets, 1))
    prompt = (
        f"TARGET JOB: {job.get('title', '')}\n"
        f"SKILLS THE JOB WANTS: {wanted}\n\n"
        f"Rewrite these {len(bullets)} bullets. Surface any of the target "
        f"skills that the bullet genuinely demonstrates, but invent nothing.\n\n"
        f"{numbered}"
    )
    return LLMRequest(system=_SYSTEM, prompt=prompt, schema=REWRITE_SCHEMA,
                      max_tokens=3000, effort=effort)


# ======================================================
# PUBLIC API
# ======================================================
def rewrite_for_job(resume: MasterResume, job: dict, provider: LLMProvider,
                    effort: str = "high") -> RewriteResult:
    """
    Returns a copy of the resume with its bullets rewritten for the job.

    Never raises and never fabricates: without a provider the resume comes back
    unchanged; a rewrite that fails the fabrication check is dropped and the
    original bullet kept. The passed-in resume is never mutated.
    """
    tailored = copy.deepcopy(resume)
    result = RewriteResult(resume=tailored)

    locations = _bullet_locations(tailored)
    if not locations or not provider.is_available():
        return result

    resume_text = resume.full_text()
    originals = [entry.bullets[index] for entry, index in locations]

    try:
        response = provider.complete(_build_request(originals, job, effort))
    except LLMUnavailable as error:
        logging.info("Keeping your resume as written — AI rewrite "
                     "unavailable: %s", error)
        return result

    rewrites = response.data.get("rewrites", [])
    if len(rewrites) != len(originals):
        logging.warning("Model returned %d rewrites for %d bullets — keeping "
                        "your resume unchanged.", len(rewrites), len(originals))
        return result

    result.model = response.model
    result.from_cache = response.from_cache
    result.ai_used = True

    for (entry, index), original, rewrite in zip(locations, originals,
                                                 rewrites):
        rewrite = (rewrite or "").strip()
        if not rewrite or rewrite == original:
            result.kept_original += 1
            continue
        reason = verify_no_fabrication(rewrite, resume_text)
        if reason:
            logging.warning("Rejected a rewrite that %s.", reason)
            result.rejections.append(f"“{rewrite[:60]}…” — {reason}")
            result.kept_original += 1
            continue
        entry.bullets[index] = rewrite
        result.rewritten += 1

    logging.info("Rewrote %d bullet(s), kept %d, rejected %d as fabricated.",
                 result.rewritten, result.kept_original, len(result.rejections))
    return result

"""
explain.py
Turns a score into a reason: which skills earned it, what the job wants that
you do not have, and what would move the number most.

Entirely deterministic — this is the Standard-mode implementation of the score
explanation. An AI mode would take this output as input and narrate it, never
recompute the figures.
"""
from dataclasses import dataclass, field

import config
import db_handler
import skill_extractor
from resume_parser import skill_in_text


@dataclass
class ScoreExplanation:
    """Why a job scored what it scored."""
    job_key: str
    score_percent: float
    title_matches: list[str] = field(default_factory=list)
    body_matches: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    demand: dict[str, int] = field(default_factory=dict)
    points_earned: float = 0.0
    points_possible: float = 0.0
    lines: list[str] = field(default_factory=list)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _split_matched(matched_skills: str) -> tuple[list[str], list[str]]:
    """Separates stored 'Skill (title)' entries from plain body matches."""
    title_matches, body_matches = [], []
    for part in (matched_skills or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part.endswith("(title)"):
            title_matches.append(part.removesuffix("(title)").strip())
        else:
            body_matches.append(part)
    return title_matches, body_matches


def _missing_skills(job_title: str, job_text: str,
                    resume_skills: list[str]) -> list[str]:
    """Skills the advertisement asks for that the resume does not show."""
    held = {skill.lower() for skill in resume_skills}
    wanted = skill_extractor.extract_skills(job_title, job_text)
    return [skill for skill, _category, _in_title in wanted
            if skill.lower() not in held]


def _describe(explanation: ScoreExplanation, total_jobs: int) -> list[str]:
    """Renders the numbers as sentences a person can act on."""
    lines = []
    if explanation.title_matches:
        lines.append(
            f"{len(explanation.title_matches)} of your skills appear in the "
            f"job title, which count triple: "
            f"{', '.join(explanation.title_matches)}.")
    if explanation.body_matches:
        lines.append(
            f"{len(explanation.body_matches)} more appear in the description: "
            f"{', '.join(explanation.body_matches)}.")
    if not explanation.title_matches and not explanation.body_matches:
        lines.append("None of your skills appear in this advertisement, which "
                     "is why it scores zero.")

    lines.append(
        f"That is {explanation.points_earned:.0f} of "
        f"{explanation.points_possible:.0f} points — a job matching "
        f"{config.TARGET_MATCH_SKILLS} of your skills in its title scores 100.")

    if explanation.missing:
        ranked = sorted(explanation.missing,
                        key=lambda skill: explanation.demand.get(skill, 0),
                        reverse=True)
        wanted = f"It also asks for {', '.join(ranked[:4])}, which your " \
                 "resume does not mention."
        top = ranked[0]
        count = explanation.demand.get(top, 0)
        share = round(count / total_jobs * 100) if total_jobs else 0
        # A percentage that rounds to zero says less than the raw count, and a
        # thin corpus makes any share meaningless — fall back in both cases.
        if total_jobs >= config.CALIBRATION_MIN_JOBS and share >= 1:
            lines.append(
                f"{wanted} {top} is the one to learn first — it appears in "
                f"{share}% of the {total_jobs} jobs you track.")
        elif count > 1:
            lines.append(f"{wanted} {top} is the most requested of them, in "
                         f"{count} of your tracked jobs.")
        else:
            lines.append(wanted)
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def explain_job(job: dict, resume_skills: list[str]) -> ScoreExplanation:
    """
    Builds a full explanation for one stored job row.
    Expects the row shape returned by db_handler.fetch_all_jobs().
    """
    title_matches, body_matches = _split_matched(job.get("matched_skills", ""))
    body_text = job.get("description") or job.get("teaser") or ""

    explanation = ScoreExplanation(
        job_key=job.get("job_key", ""),
        score_percent=job.get("score_percent") or 0.0,
        title_matches=title_matches,
        body_matches=body_matches,
        missing=_missing_skills(job.get("title", ""), body_text, resume_skills),
    )
    explanation.points_earned = (
        len(title_matches) * config.TITLE_MATCH_WEIGHT
        + len(body_matches) * config.BODY_MATCH_WEIGHT)
    explanation.points_possible = (
        min(len(resume_skills), max(1, config.TARGET_MATCH_SKILLS))
        * config.TITLE_MATCH_WEIGHT)

    if explanation.missing:
        explanation.demand = {
            row["skill"]: row["demand"]
            for row in db_handler.skill_demand(limit=500)
            if row["skill"] in set(explanation.missing)
        }
    explanation.lines = _describe(explanation, db_handler.total_active_jobs())
    return explanation

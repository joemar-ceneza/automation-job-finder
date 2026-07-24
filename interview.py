"""
interview.py
Standard-mode interview preparation: turn one job's score into a prep sheet —
the questions you are likely to be asked, grounded in the skills the advert
wants and the gaps it exposes, each paired with a talking point drawn from your
actual resume.

Entirely deterministic, and the same shape as the rest of the apply workflow:
Standard mode computes the truth (which skills matched, which are missing) and
this module arranges it into questions and talking points. It invents nothing —
every talking point is a bullet already in your resume, or an honest prompt to
prepare one. An AI mode would take this sheet as input and expand each answer,
never change which skills you have.

It is a pure transform of (resume, job, ScoreExplanation): the caller computes
the explanation, so this module never touches the database and is easy to trust
and to test.
"""
import re
from dataclasses import dataclass, field

from explain import ScoreExplanation
from resume_model import MasterResume
from resume_parser import skill_in_text

# A bullet citing a metric is stronger evidence, so it makes the better talking
# point. A percentage is best; any other number is good — but a bare four-digit
# year (a graduation or certification date) is not "measurable impact", so it is
# stripped out before deciding, or an education line would be cited as an
# achievement.
_PERCENT = re.compile(r"\d+\s*%|\bpercent\b", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ANY_DIGIT = re.compile(r"\d")


def _impact_rank(bullet: str) -> int:
    """0 = no metric, 1 = a number, 2 = a percentage. Bare years don't count."""
    if _PERCENT.search(bullet):
        return 2
    return 1 if _ANY_DIGIT.search(_YEAR.sub(" ", bullet)) else 0

# How many of each kind to include, so the sheet stays a page rather than a
# transcript of every skill.
_MAX_STRENGTHS = 5
_MAX_GAPS = 3


@dataclass
class TalkingPoint:
    """A skill the job wants that you have, and the bullet that proves it."""
    skill: str
    bullet: str = ""            # "" when the skill is listed but not evidenced


@dataclass
class Question:
    """One likely interview question, with how to approach it."""
    category: str               # Experience | Gap | Behavioural
    prompt: str
    hint: str = ""


@dataclass
class InterviewPrep:
    """A prep sheet for one job."""
    job_key: str
    position: str
    company: str
    strengths: list[TalkingPoint] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _bullet_for_skill(resume: MasterResume, skill: str) -> str:
    """
    The best resume bullet evidencing a skill: one that mentions it, breaking
    ties toward a bullet that cites a number. Empty when no bullet mentions it
    — the skill is on the list but never demonstrated in the experience.
    """
    mentioning = [bullet for bullet in resume.all_bullets()
                  if skill_in_text(skill, bullet.lower())]
    if not mentioning:
        return ""
    return max(mentioning, key=_impact_rank)


def _quantified_bullet(resume: MasterResume) -> str:
    """
    The resume bullet with the strongest metric — evidence of measurable
    impact. Empty when nothing cites a real number, so the measurable-impact
    question is dropped rather than pointed at a graduation year.
    """
    best = max(resume.all_bullets(), key=_impact_rank, default="")
    return best if _impact_rank(best) > 0 else ""


def _strengths(resume: MasterResume,
               explanation: ScoreExplanation) -> list[TalkingPoint]:
    """
    The skills to lead with: those the advert names and the resume shows, title
    matches first (they count triple in the score), each with its evidence.
    """
    matched = explanation.title_matches + explanation.body_matches
    return [TalkingPoint(skill=skill, bullet=_bullet_for_skill(resume, skill))
            for skill in matched[:_MAX_STRENGTHS]]


def _ranked_gaps(explanation: ScoreExplanation) -> list[str]:
    """Missing skills, most in-demand across your tracked jobs first."""
    return sorted(explanation.missing,
                  key=lambda skill: explanation.demand.get(skill, 0),
                  reverse=True)[:_MAX_GAPS]


def _experience_questions(strengths: list[TalkingPoint]) -> list[Question]:
    questions = []
    for point in strengths:
        hint = (f"Lead with: “{point.bullet}”" if point.bullet
                else f"You list {point.skill}; have one concrete example ready "
                     "of where you applied it.")
        questions.append(Question(
            category="Experience",
            prompt=f"Tell me about your experience with {point.skill}.",
            hint=hint))
    return questions


def _gap_questions(gaps: list[str]) -> list[Question]:
    return [Question(
        category="Gap",
        prompt=f"This role uses {skill}, which isn't on your resume. How would "
               "you get up to speed?",
        hint="Be honest you haven't used it, name the closest thing you have, "
             "and give a concrete example of picking up a tool quickly.")
        for skill in gaps]


def _behavioural_questions(prep: InterviewPrep,
                           resume: MasterResume) -> list[Question]:
    questions = [Question(
        category="Behavioural",
        prompt=f"Why do you want the {prep.position} role"
               + (f" at {prep.company}" if prep.company else "") + "?",
        hint="Tie your strongest match to what they build — "
             + (f"lead with {prep.strengths[0].skill}."
                if prep.strengths else "focus on what drew you to the role."))]

    if prep.strengths:
        top = prep.strengths[0]
        questions.append(Question(
            category="Behavioural",
            prompt="What's your greatest professional strength?",
            hint=(f"{top.skill} — " + (f"“{top.bullet}”" if top.bullet
                  else "give a concrete example."))))

    quantified = _quantified_bullet(resume)
    if quantified:
        questions.append(Question(
            category="Behavioural",
            prompt="Tell me about a time you made a measurable impact.",
            hint=f"“{quantified}”"))

    closing_topic = (prep.gaps[0] if prep.gaps
                     else prep.strengths[0].skill if prep.strengths
                     else "the team")
    questions.append(Question(
        category="Behavioural",
        prompt="Do you have any questions for us?",
        hint=f"Ask how the team uses {closing_topic}, and what success in the "
             "first few months looks like."))
    return questions


def _describe(prep: InterviewPrep) -> list[str]:
    """Renders the sheet as lines a person can read in the terminal."""
    lines = []
    if prep.strengths:
        lines.append("Lead with these — the advert asks for them and your "
                     "resume shows them:")
        for point in prep.strengths:
            lines.append(f"  • {point.skill}"
                         + (f" — “{point.bullet}”" if point.bullet else ""))
    else:
        lines.append("This advert names none of your skills — prepare to show "
                     "transferable experience and genuine interest.")

    if prep.gaps:
        lines.append("")
        lines.append("Expect to be pressed on what you don't list: "
                     + ", ".join(prep.gaps) + ".")

    lines.append("")
    lines.append("Likely questions:")
    for question in prep.questions:
        lines.append(f"  [{question.category}] {question.prompt}")
        if question.hint:
            lines.append(f"      → {question.hint}")
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def prepare(resume: MasterResume, job: dict,
            explanation: ScoreExplanation) -> InterviewPrep:
    """
    Builds an interview prep sheet from one resume and one scored job. Pure and
    deterministic: the caller supplies the deterministic explanation, and every
    talking point here is a bullet already in the resume — nothing is invented.
    """
    prep = InterviewPrep(
        job_key=explanation.job_key,
        position=job.get("title") or "the role",
        company=job.get("company") or "",
    )
    prep.strengths = _strengths(resume, explanation)
    prep.gaps = _ranked_gaps(explanation)
    prep.questions = (_experience_questions(prep.strengths)
                      + _gap_questions(prep.gaps)
                      + _behavioural_questions(prep, resume))
    prep.lines = _describe(prep)
    return prep

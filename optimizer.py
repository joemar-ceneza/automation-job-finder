"""
optimizer.py
Standard-mode resume optimiser: tailors a master resume to one job without
rewriting a single word.

What it does is restructure and report — reorder sections so the relevant ones
lead, promote the bullets that already evidence what the job asks for, surface
skills you have but did not mention, and score the resume against a rubric.
What it deliberately does not do is change your wording; that is AI mode's job,
and it needs a verifier before it can be trusted.
"""
import re
from dataclasses import dataclass, field

import config
import skill_extractor
from resume_model import Entry, MasterResume, Section
from resume_parser import skill_in_text

# A bullet carrying a number, percentage, or money figure is evidence rather
# than assertion, and both recruiters and ATS ranking favour it.
_QUANTIFIED = re.compile(r"\d+\s*%|\b\d[\d,.]*\b|\b(?:php|usd|\$|₱)\s*\d",
                         re.IGNORECASE)
# A date range on an entry: "Jan 2023 - Present", "2019 – 2021".
_DATE_RANGE = re.compile(
    r"(?:19|20)\d{2}|present|current", re.IGNORECASE)
_LONG_BULLET_WORDS = 42


@dataclass
class Check:
    """One rubric line."""
    name: str
    points: float
    max_points: float
    detail: str

    @property
    def passed(self) -> bool:
        return self.points >= self.max_points


@dataclass
class OptimisedResume:
    """The tailored resume plus everything worth telling the user about it."""
    resume: MasterResume
    ats_score: float = 0.0
    checks: list[Check] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    unmentioned_skills: list[str] = field(default_factory=list)
    promoted_bullets: int = 0
    section_order: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)


# ======================================================
# JOB REQUIREMENTS
# ======================================================
def _job_skills(job: dict) -> list[str]:
    """Canonical skills the advertisement asks for."""
    body = job.get("description") or job.get("teaser") or ""
    return [skill for skill, _category, _in_title
            in skill_extractor.extract_skills(job.get("title", ""), body)]


# ======================================================
# RESTRUCTURING
# ======================================================
def _section_relevance(section: Section, wanted: list[str]) -> int:
    """How many of the job's skills this section evidences."""
    text = section.text().lower()
    return sum(1 for skill in wanted if skill_in_text(skill, text))


def _reorder_sections(resume: MasterResume,
                      wanted: list[str]) -> tuple[MasterResume, list[str]]:
    """
    Puts the sections that evidence the job's requirements first, keeping the
    configured priority as the tie-break. Never drops or merges a section.
    """
    priority = [name.lower() for name in config.RESUME_SECTION_PRIORITY]

    def rank(item: tuple[int, Section]) -> tuple[int, int, int]:
        index, section = item
        key = section.name.strip().lower()
        configured = priority.index(key) if key in priority else len(priority)
        # Negative relevance so more relevant sorts earlier.
        return (-_section_relevance(section, wanted), configured, index)

    ordered = [section for _index, section
               in sorted(enumerate(resume.sections), key=rank)]
    return (MasterResume(contact=resume.contact, sections=ordered),
            [section.name for section in ordered])


def _promote_bullets(resume: MasterResume, wanted: list[str]) -> int:
    """
    Within each entry, lifts bullets that evidence the job's requirements to
    the top. A stable sort, so bullets of equal relevance keep their order and
    the entry still reads chronologically.
    """
    promoted = 0
    for section in resume.sections:
        for entry in section.entries:
            if len(entry.bullets) < 2:
                continue
            scored = [
                (sum(1 for skill in wanted
                     if skill_in_text(skill, bullet.lower())), index, bullet)
                for index, bullet in enumerate(entry.bullets)
            ]
            reordered = [bullet for _relevance, _index, bullet
                         in sorted(scored, key=lambda item: (-item[0], item[1]))]
            if reordered != entry.bullets:
                promoted += sum(1 for before, after
                                in zip(entry.bullets, reordered)
                                if before != after)
                entry.bullets = reordered
    return promoted


# ======================================================
# ATS RUBRIC
# ======================================================
def _check_keyword_coverage(resume: MasterResume, wanted: list[str]) -> Check:
    if not wanted:
        return Check("Keyword coverage", 30, 30,
                     "The advertisement names no recognised skills.")
    text = resume.full_text().lower()
    hits = [skill for skill in wanted if skill_in_text(skill, text)]
    share = len(hits) / len(wanted)
    return Check("Keyword coverage", round(30 * share, 1), 30,
                 f"{len(hits)} of {len(wanted)} skills the job names appear "
                 f"in your resume.")


def _check_headings(resume: MasterResume) -> Check:
    present = {section.name.strip().lower() for section in resume.sections}
    expected = {"experience", "education", "skills"}
    found = expected & present
    return Check("Standard headings", round(12 * len(found) / 3, 1), 12,
                 f"Found {', '.join(sorted(found)) or 'none'}. Parsers look "
                 f"for Experience, Education, and Skills by name.")


def _check_dates(resume: MasterResume) -> Check:
    entries = [entry for section in resume.sections
               for entry in section.entries
               if entry.title and section.kind == "entries"]
    if not entries:
        return Check("Parseable dates", 0, 12, "No dated entries found.")
    dated = [entry for entry in entries if _DATE_RANGE.search(entry.meta)]
    share = len(dated) / len(entries)
    return Check("Parseable dates", round(12 * share, 1), 12,
                 f"{len(dated)} of {len(entries)} entries carry a date range.")


def _check_contact(resume: MasterResume) -> Check:
    contact = resume.contact
    have = [bool(contact.name), bool(contact.email), bool(contact.phone)]
    missing = [label for label, present
               in zip(("name", "email", "phone"), have) if not present]
    return Check("Contact details", round(10 * sum(have) / 3, 1), 10,
                 "Complete." if not missing
                 else f"Missing: {', '.join(missing)}.")


def _check_quantified(resume: MasterResume) -> Check:
    bullets = resume.all_bullets()
    if not bullets:
        return Check("Quantified achievements", 0, 20, "No bullets found.")
    quantified = [bullet for bullet in bullets if _QUANTIFIED.search(bullet)]
    share = len(quantified) / len(bullets)
    # Full marks at a third quantified — every bullet carrying a number reads
    # as padding rather than evidence.
    return Check("Quantified achievements",
                 round(min(1.0, share / 0.33) * 20, 1), 20,
                 f"{len(quantified)} of {len(bullets)} bullets cite a number. "
                 f"Aim for roughly a third.")


def _check_bullet_length(resume: MasterResume) -> Check:
    bullets = resume.all_bullets()
    if not bullets:
        return Check("Bullet length", 0, 8, "No bullets found.")
    long_ones = [bullet for bullet in bullets
                 if len(bullet.split()) > _LONG_BULLET_WORDS]
    share = 1 - (len(long_ones) / len(bullets))
    return Check("Bullet length", round(8 * share, 1), 8,
                 "All bullets are a readable length." if not long_ones
                 else f"{len(long_ones)} bullet(s) run over "
                      f"{_LONG_BULLET_WORDS} words.")


def _check_skills_section(resume: MasterResume) -> Check:
    listed = resume.listed_skills()
    return Check("Skills section", 8 if listed else 0, 8,
                 f"{len(listed)} skills listed." if listed
                 else "No Skills section — parsers rely on it heavily.")


def ats_report(resume: MasterResume, wanted: list[str]) -> tuple[float,
                                                                 list[Check]]:
    """
    Scores the resume out of 100 against checks that are computable from the
    document itself.

    Checks about the rendered file — text is extractable, layout is single
    column — are omitted on purpose rather than awarded for free: documents.py
    guarantees both, so scoring them would inflate every result by 40 points
    and tell the user nothing they can act on.
    """
    checks = [
        _check_keyword_coverage(resume, wanted),
        _check_quantified(resume),
        _check_headings(resume),
        _check_dates(resume),
        _check_contact(resume),
        _check_skills_section(resume),
        _check_bullet_length(resume),
    ]
    return round(sum(check.points for check in checks), 1), checks


# ======================================================
# COMPARISON
# ======================================================
@dataclass
class ResumeRanking:
    """How one resume fares against one job, for side-by-side comparison."""
    name: str
    match_percent: float
    ats_score: float
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unmentioned: list[str] = field(default_factory=list)
    bullets: int = 0

    @property
    def combined(self) -> float:
        """
        Ranking key: how well the resume matches, tempered by how readable a
        parser will find it. Weighted toward the match, because a perfectly
        formatted resume for the wrong job is still the wrong resume.
        """
        return round(self.match_percent * 0.7 + self.ats_score * 0.3, 1)


def _match_percent(resume: MasterResume, wanted: list[str]) -> float:
    """Share of the job's stated skills this resume evidences."""
    if not wanted:
        return 0.0
    text = resume.full_text().lower()
    hits = sum(1 for skill in wanted if skill_in_text(skill, text))
    return round(hits / len(wanted) * 100, 1)


def compare(job: dict, named_resumes: list[tuple[str, MasterResume]]
            ) -> list[ResumeRanking]:
    """
    Ranks several resumes against one job, best first.

    Every figure here is arithmetic over the same matching the scorer uses —
    no model can rank these more honestly than the numbers already do.
    """
    wanted = _job_skills(job)
    rankings = []
    for name, resume in named_resumes:
        text = resume.full_text().lower()
        listed = {skill.lower() for skill in resume.listed_skills()}
        matched = [skill for skill in wanted if skill_in_text(skill, text)]
        score, _checks = ats_report(resume, wanted)
        rankings.append(ResumeRanking(
            name=name,
            match_percent=_match_percent(resume, wanted),
            ats_score=score,
            matched=matched,
            missing=[skill for skill in wanted
                     if not skill_in_text(skill, text)],
            unmentioned=[skill for skill in matched
                         if skill.lower() not in listed],
            bullets=len(resume.all_bullets()),
        ))
    return sorted(rankings, key=lambda ranking: -ranking.combined)


# ======================================================
# PUBLIC API
# ======================================================
def optimise(resume: MasterResume, job: dict) -> OptimisedResume:
    """
    Tailors the resume to one job by restructuring only. Wording is untouched.
    """
    wanted = _job_skills(job)
    resume_text = resume.full_text().lower()
    listed = {skill.lower() for skill in resume.listed_skills()}

    tailored, order = _reorder_sections(resume, wanted)
    promoted = _promote_bullets(tailored, wanted)
    score, checks = ats_report(tailored, wanted)

    matched = [skill for skill in wanted if skill_in_text(skill, resume_text)]
    missing = [skill for skill in wanted
               if not skill_in_text(skill, resume_text)]
    # Present in your experience but absent from the Skills list — free marks,
    # because a parser reading only that section never sees them.
    unmentioned = [skill for skill in matched if skill.lower() not in listed]

    result = OptimisedResume(
        resume=tailored, ats_score=score, checks=checks,
        matched_skills=matched, missing_skills=missing,
        unmentioned_skills=unmentioned, promoted_bullets=promoted,
        section_order=order)

    if order != [section.name for section in resume.sections]:
        result.changes.append(
            f"Reordered sections to lead with {order[0]}.")
    if promoted:
        result.changes.append(
            f"Moved {promoted} relevant bullet(s) higher within their roles.")
    if unmentioned:
        result.changes.append(
            f"Add to your Skills section — evidenced in your experience but "
            f"not listed: {', '.join(unmentioned)}.")
    if missing:
        result.changes.append(
            f"The job asks for {', '.join(missing[:5])}, which your resume "
            f"does not mention anywhere.")
    if not result.changes:
        result.changes.append("Already well matched — nothing to restructure.")
    return result

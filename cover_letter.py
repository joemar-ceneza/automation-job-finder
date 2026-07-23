"""
cover_letter.py
Standard-mode cover letters: professional templates filled from your master
resume and the job advert.

Everything chosen here is deterministic — the three skills named, the role
quoted, and the achievement highlighted are all picked by the same matching
the scorer uses, not invented. Nothing is written that is not already in your
resume.

Be clear-eyed about what this is: a template letter reads like a template
letter. Its value is speed when a letter is required but unlikely to be read
closely. AI mode exists for the applications worth writing properly.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from string import Template

import config
import skill_extractor
from resume_model import Contact, Entry, MasterResume
from resume_parser import skill_in_text

# Bullets citing a number are evidence rather than assertion, so they make the
# better highlight when relevance ties.
_QUANTIFIED = re.compile(r"\d+\s*%|\b\d[\d,.]*\b", re.IGNORECASE)
_COMMENT = re.compile(r"^\s*#")
# A headline that is really a postal address — see _headline().
_LOOKS_LIKE_ADDRESS = re.compile(
    r"\b(city|province|philippines|metro manila)\b", re.IGNORECASE)


@dataclass
class CoverLetter:
    """A finished letter, ready to render."""
    company: str
    position: str
    recipient: str
    sender: Contact
    paragraphs: list[str] = field(default_factory=list)
    tone: str = "direct"
    letter_date: str = ""
    skills_used: list[str] = field(default_factory=list)

    def salutation(self) -> str:
        return f"Dear {self.recipient},"

    def to_text(self) -> str:
        """The letter as plain text, in the order it should be read."""
        blocks = [self.letter_date, ""]
        if self.company:
            blocks.extend([self.company, ""])
        blocks.extend([self.salutation(), ""])
        for paragraph in self.paragraphs:
            blocks.extend([paragraph, ""])
        blocks.extend(["Sincerely,", self.sender.name])
        detail = self.sender.detail_line()
        if detail:
            blocks.append(detail)
        return "\n".join(blocks).strip() + "\n"

    def to_markdown(self) -> str:
        lines = [f"**{self.letter_date}**", ""]
        if self.company:
            lines.extend([self.company, ""])
        lines.extend([self.salutation(), ""])
        for paragraph in self.paragraphs:
            lines.extend([paragraph, ""])
        lines.extend(["Sincerely,  ", f"**{self.sender.name}**"])
        detail = self.sender.detail_line()
        if detail:
            lines.append(f"  \n{detail}")
        return "\n".join(lines).strip() + "\n"


# ======================================================
# TEMPLATES
# ======================================================
def available_tones() -> list[str]:
    """Template names found on disk, so adding a file adds a tone."""
    if not os.path.isdir(config.COVER_LETTER_TEMPLATE_DIR):
        return []
    return sorted(
        os.path.splitext(entry)[0]
        for entry in os.listdir(config.COVER_LETTER_TEMPLATE_DIR)
        if entry.endswith(".txt"))


def _load_template(tone: str) -> list[str]:
    """Reads one template into paragraphs, dropping comment lines."""
    path = os.path.join(config.COVER_LETTER_TEMPLATE_DIR, f"{tone}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No template for tone {tone!r}. Available: "
            f"{', '.join(available_tones()) or 'none'}")
    with open(path, "r", encoding="utf-8") as handle:
        body = "\n".join(line for line in handle.read().splitlines()
                         if not _COMMENT.match(line))
    return [re.sub(r"\s+", " ", block).strip()
            for block in body.split("\n\n") if block.strip()]


# ======================================================
# CONTENT SELECTION
# ======================================================
def _job_skills(job: dict) -> list[str]:
    body = job.get("description") or job.get("teaser") or ""
    return [skill for skill, _category, _in_title
            in skill_extractor.extract_skills(job.get("title", ""), body)]


def _matched_skills(resume: MasterResume, job: dict) -> list[str]:
    """
    Skills the advert names that the resume evidences, most prominent first.
    Skills named in the job title lead, since those are what it is really for.
    """
    title = (job.get("title") or "").lower()
    resume_text = resume.full_text().lower()
    matched = [skill for skill in _job_skills(job)
               if skill_in_text(skill, resume_text)]
    return sorted(matched,
                  key=lambda skill: (not skill_in_text(skill, title), skill))


def _most_relevant_entry(resume: MasterResume,
                         skills: list[str]) -> Entry | None:
    """
    The role that best evidences what this job asks for.

    Restricted to experience sections: a summary paragraph or a degree is not
    a job, and quoting one as "my role at my current employer" is exactly the
    kind of nonsense that makes a generated letter unsendable.
    """
    candidates = [entry for section in resume.sections
                  if section.is_experience
                  for entry in section.entries if entry.title]
    if not candidates:
        return None

    def relevance(entry: Entry) -> int:
        text = entry.text().lower()
        return sum(1 for skill in skills if skill_in_text(skill, text))

    best = max(candidates, key=relevance)
    # No overlap at all: fall back to the first listed role, which is normally
    # the most recent, rather than an arbitrary one.
    return best if relevance(best) else candidates[0]


def _best_bullet(entry: Entry | None, skills: list[str]) -> str:
    """
    The single achievement to lead with: most relevant, breaking ties toward
    one that cites a number.
    """
    if entry is None or not entry.bullets:
        return ""

    def rank(bullet: str) -> tuple[int, int]:
        text = bullet.lower()
        relevance = sum(1 for skill in skills if skill_in_text(skill, text))
        return (relevance, 1 if _QUANTIFIED.search(bullet) else 0)

    return max(entry.bullets, key=rank)


def _headline(contact: Contact) -> str:
    """
    The professional title to describe yourself with. An address that slipped
    into the headline during import would otherwise produce "I work as a
    Quezon City, Metro Manila, Philippines".
    """
    headline = (contact.headline or "").strip()
    if not headline or _LOOKS_LIKE_ADDRESS.search(headline):
        return "developer"
    return headline


def _readable_list(items: list[str]) -> str:
    """'a, b and c' — reads as prose rather than as a CSV dump."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


# ======================================================
# PUBLIC API
# ======================================================
def compose(resume: MasterResume, job: dict, tone: str = "direct",
            recipient: str | None = None) -> CoverLetter:
    """
    Builds a letter for one job from one resume. Every inserted detail comes
    from the resume or the advert; nothing is invented.
    """
    matched = _matched_skills(resume, job)
    entry = _most_relevant_entry(resume, matched)
    highlight = _best_bullet(entry, matched)

    values = {
        "company": job.get("company") or "your company",
        "position": job.get("title") or "the role",
        "top_skills": _readable_list(matched[:3]) or "the tools you list",
        "matched_count": str(len(matched)),
        "recent_role": (entry.title if entry else "my current role"),
        "recent_employer": (entry.organisation if entry and entry.organisation
                            else "my current employer"),
        "highlight": highlight or "",
        "headline": _headline(resume.contact),
        "name": resume.contact.name,
    }

    paragraphs = []
    for block in _load_template(tone):
        # safe_substitute leaves an unknown placeholder alone instead of
        # raising, so a hand-edited template never breaks the run.
        text = Template(block).safe_substitute(values).strip()
        if text:
            paragraphs.append(text)

    letter = CoverLetter(
        company=job.get("company") or "",
        position=job.get("title") or "",
        recipient=recipient or config.COVER_LETTER_RECIPIENT,
        sender=resume.contact,
        paragraphs=paragraphs,
        tone=tone,
        letter_date=date.today().strftime("%d %B %Y"),
        skills_used=matched[:3],
    )
    if not matched:
        logging.warning("The advert names no skills your resume evidences — "
                        "the letter will be generic. Consider whether this "
                        "job is worth applying to.")
    logging.info("Composed a %s cover letter for %s at %s (%d matched skills).",
                 tone, letter.position, letter.company or "unknown",
                 len(matched))
    return letter

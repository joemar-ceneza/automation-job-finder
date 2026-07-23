"""
resume_import.py
Bootstraps a master resume from an existing PDF — once.

PDF text extraction loses structure, so this is a best-effort draft that needs
reviewing, exactly like --generate-skills. The point is to get you to an
editable Markdown file quickly; after that the Markdown is the source of truth
and the PDF is never parsed again.
"""
import logging
import re

import resume_model
from resume_model import Contact, Entry, MasterResume, Section

# Headings a resume commonly uses, mapped to the name we store them under.
_KNOWN_SECTIONS = {
    "summary": "Summary", "profile": "Summary", "objective": "Summary",
    "about": "Summary", "about me": "Summary",
    "skills": "Skills", "technical skills": "Skills",
    "core skills": "Skills", "technologies": "Skills",
    "tech stack": "Skills", "skills & tools": "Skills",
    "experience": "Experience", "work experience": "Experience",
    "employment": "Experience", "professional experience": "Experience",
    "work history": "Experience",
    "education": "Education", "academic background": "Education",
    "projects": "Projects", "personal projects": "Projects",
    "certifications": "Certifications", "certificates": "Certifications",
}

# A line that is probably a heading: short, no sentence punctuation.
_HEADING_LIKE = re.compile(r"^[A-Z][A-Za-z&/ ]{2,40}$")
_BULLET_START = re.compile(r"^\s*[-*•▪·]\s+")
# "Jan 2023 - Present", "2021 – 2024", "01/2020 to 03/2022"
_DATE_RANGE = re.compile(
    r"(19|20)\d{2}|present|current|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# "Front-End Development:" / "Languages:" at the start of a skills line.
_SKILL_CATEGORY = re.compile(r"^[A-Z][A-Za-z&/\- ]{2,40}:\s*")


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _canonical_heading(line: str) -> str | None:
    """The section name this line introduces, if it introduces one."""
    key = line.strip().strip(":").lower()
    if key in _KNOWN_SECTIONS:
        return _KNOWN_SECTIONS[key]
    # An all-caps short line is almost always a heading in a resume.
    stripped = line.strip().strip(":")
    if (stripped.isupper() and 2 < len(stripped) <= 40
            and not _DATE_RANGE.search(stripped)):
        return stripped.title()
    return None


def _looks_like_entry_heading(line: str) -> bool:
    """A job or degree title line, rather than a bullet or a date line."""
    stripped = line.strip()
    if not stripped or _BULLET_START.match(stripped):
        return False
    if len(stripped) > 90:
        return False
    return bool(_HEADING_LIKE.match(stripped)
                or re.search(r"\s+(?:—|–|-|at|@)\s+", stripped))


def _parse_contact_block(lines: list[str]) -> Contact:
    """Reads the top of the resume, before the first recognised heading."""
    contact = Contact()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not contact.name:
            contact.name = stripped
            continue
        if not contact.email and (found := _EMAIL.search(stripped)):
            contact.email = found.group()
        if not contact.phone and (found := _PHONE.search(stripped)):
            contact.phone = found.group().strip()
        has_details = _EMAIL.search(stripped) or _PHONE.search(stripped)
        if not has_details and not contact.headline and len(stripped) < 80:
            contact.headline = stripped
    return contact


def _split_skill_line(line: str) -> list[str]:
    """
    Splits one skills line into individual skills, dropping any leading
    category label. Resumes group skills as "Front-End Development: React,
    Next.js", and keeping the label would fuse it onto the first skill.
    """
    text = _SKILL_CATEGORY.sub("", line.strip(), count=1)
    return [item.strip(" .;") for item in re.split(r"[,;|•·]", text)
            if item.strip(" .;")]


def _build_section(name: str, body: list[str]) -> Section:
    """Turns a run of raw lines into prose, a list, or dated entries."""
    section = Section(name=name)
    if section.kind == "list":
        # Line by line, not joined: category labels start new lines, and
        # joining first would merge the last skill of one group into the
        # label of the next.
        for line in body:
            if line.strip():
                section.items.extend(_split_skill_line(line))
        return section
    if section.kind == "prose":
        section.prose = " ".join(line.strip() for line in body if line.strip())
        return section

    current: Entry | None = None
    for line in body:
        stripped = line.strip()
        if not stripped:
            continue
        if _BULLET_START.match(stripped):
            if current is None:
                current = Entry(title="(untitled)")
            current.bullets.append(_BULLET_START.sub("", stripped).strip())
        elif _looks_like_entry_heading(stripped) and not _DATE_RANGE.search(
                stripped):
            if current is not None:
                section.entries.append(current)
            pieces = re.split(r"\s+(?:—|–|-|at|@)\s+", stripped, maxsplit=1)
            current = Entry(title=pieces[0].strip(),
                            organisation=(pieces[1].strip()
                                          if len(pieces) > 1 else ""))
        elif current is not None and not current.meta and not current.bullets \
                and _DATE_RANGE.search(stripped):
            current.meta = stripped
        elif current is not None and current.bullets:
            # A PDF wraps long bullets across lines, and the continuation
            # carries no marker. Treating it as a new bullet would cut every
            # long achievement in half mid-sentence.
            current.bullets[-1] = f"{current.bullets[-1]} {stripped}"
        elif current is not None:
            current.bullets.append(stripped)
        else:
            current = Entry(title=stripped)
    if current is not None:
        section.entries.append(current)
    return section


# ======================================================
# PUBLIC API
# ======================================================
def from_resume_text(text: str) -> MasterResume:
    """Best-effort conversion of extracted PDF text into the master model."""
    header: list[str] = []
    sections: list[Section] = []
    current_name: str | None = None
    current_body: list[str] = []

    for raw_line in (text or "").splitlines():
        heading = _canonical_heading(raw_line)
        if heading:
            if current_name is not None:
                sections.append(_build_section(current_name, current_body))
            current_name, current_body = heading, []
        elif current_name is None:
            header.append(raw_line)
        else:
            current_body.append(raw_line)
    if current_name is not None:
        sections.append(_build_section(current_name, current_body))

    resume = MasterResume(contact=_parse_contact_block(header),
                          sections=sections)
    logging.info("Imported %d section(s) and %d bullet(s) from the PDF.",
                 len(resume.sections), len(resume.all_bullets()))
    return resume


def write_draft(resume: MasterResume, path: str) -> None:
    """
    Writes the imported resume with a review banner on top. The banner is a
    comment, so re-reading the file ignores it.
    """
    banner = (
        "<!-- Draft imported from your PDF — review before relying on it.\n"
        "     PDF text extraction loses structure, so section boundaries and\n"
        "     bullet grouping are guesses. Fix anything wrong here; from now\n"
        "     on THIS file is the source of truth and the PDF is not read\n"
        "     again. Keep the heading levels (#, ##, ###) as they are. -->\n\n")
    import os
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(banner + resume.to_markdown())
    logging.info("Wrote master resume draft to %s", path)


def load_or_import(master_path: str, pdf_path: str | None) -> MasterResume:
    """
    Reads the master resume, falling back to importing the PDF when no master
    exists yet. Callers get a usable resume either way.
    """
    import os
    if os.path.exists(master_path):
        return resume_model.load(master_path)
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"No master resume at {master_path} and no PDF to import from. "
            f"Run: python main.py <resume.pdf> --import-resume")
    import resume_parser
    logging.warning("No master resume at %s — importing from %s.",
                    master_path, pdf_path)
    return from_resume_text(resume_parser.extract_text_from_pdf(pdf_path))

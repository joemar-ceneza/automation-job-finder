"""
resume_model.py
The master resume: a structured document that is the source of truth, held as
Markdown so it stays readable and hand-editable.

Why this exists. A PDF parsed to flat text cannot be reordered, re-tagged, or
re-rendered — the structure was never captured, and every round trip through a
PDF loses more of it. Keeping the resume as structured Markdown means Standard
mode can genuinely reorder sections and promote bullets, AI mode can rewrite
bullet text in place, and export stops being lossy.

Parsing is deliberately dependency-free: the format below is simple enough that
a small reader beats pulling in a Markdown library, and Markdown is itself one
of the required export formats, so the master file is already a deliverable.

Format
------
    # Name
    Headline
    email · phone · location · links

    ## Summary
    Prose paragraph.

    ## Skills
    Python, React.js, MongoDB

    ## Experience

    ### Job Title — Employer
    Location · Jan 2023 - Present
    - Achievement bullet.
    - Another bullet.
"""
import re
from dataclasses import dataclass, field

# A "### Title — Employer" heading. Accepts an em dash, en dash, or " - ".
_ENTRY_SPLIT = re.compile(r"\s+(?:—|–|-)\s+")
# Contact details are separated by a middot or a pipe.
_CONTACT_SPLIT = re.compile(r"\s*[·|]\s*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")
_LINK = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|dev|io|ph|net|org)"
                   r"(?:/\S*)?", re.IGNORECASE)
# Review banners and the user's own notes live in HTML comments.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Section kinds are decided by keyword, not exact name: real resumes write
# "Professional Summary" and "Technical Skills", and an exact-match set would
# treat both as dated entries — which turns a summary paragraph into a fake
# job whose text then gets quoted as an employer.
PROSE_KEYWORDS = ("summary", "objective", "profile", "about")
LIST_KEYWORDS = ("skill", "technolog", "tech stack", "tools", "competenc")
EXPERIENCE_KEYWORDS = ("experience", "employment", "work history", "career")


# ======================================================
# MODEL
# ======================================================
@dataclass
class Contact:
    """Everything above the first section heading."""
    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = field(default_factory=list)

    def detail_line(self) -> str:
        """Renders the contact details back to a single Markdown line."""
        parts = [self.email, self.phone, self.location, *self.links]
        return " · ".join(part for part in parts if part)


@dataclass
class Entry:
    """One dated item: a job, a degree, or a project."""
    title: str = ""
    organisation: str = ""
    meta: str = ""                                  # "Manila · 2023 - Present"
    bullets: list[str] = field(default_factory=list)

    def heading(self) -> str:
        if self.organisation:
            return f"{self.title} — {self.organisation}"
        return self.title

    def text(self) -> str:
        """All searchable text in this entry."""
        return " ".join([self.title, self.organisation, self.meta,
                         *self.bullets])


@dataclass
class Section:
    """A "## " block. Holds prose, a list, entries, or a mix."""
    name: str = ""
    prose: str = ""
    items: list[str] = field(default_factory=list)   # Skills-style lists
    entries: list[Entry] = field(default_factory=list)

    @property
    def kind(self) -> str:
        key = self.name.strip().lower()
        if any(word in key for word in PROSE_KEYWORDS):
            return "prose"
        if any(word in key for word in LIST_KEYWORDS):
            return "list"
        return "entries"

    @property
    def is_experience(self) -> bool:
        """True for the sections that hold actual jobs."""
        key = self.name.strip().lower()
        return any(word in key for word in EXPERIENCE_KEYWORDS)

    def text(self) -> str:
        return " ".join([self.prose, ", ".join(self.items),
                         *(entry.text() for entry in self.entries)])


@dataclass
class MasterResume:
    """The whole document."""
    contact: Contact = field(default_factory=Contact)
    sections: list[Section] = field(default_factory=list)

    # --- queries ---------------------------------------------------------
    def section(self, name: str) -> Section | None:
        """Finds a section by name, case-insensitively."""
        target = name.strip().lower()
        return next((section for section in self.sections
                     if section.name.strip().lower() == target), None)

    def listed_skills(self) -> list[str]:
        """Whatever the Skills-style sections declare, in order."""
        skills: list[str] = []
        for section in self.sections:
            if section.kind == "list":
                skills.extend(section.items)
        return skills

    def all_bullets(self) -> list[str]:
        return [bullet for section in self.sections
                for entry in section.entries for bullet in entry.bullets]

    def full_text(self) -> str:
        """Everything, for skill matching against the resume as a whole."""
        return " ".join([self.contact.name, self.contact.headline,
                         *(section.text() for section in self.sections)])

    # --- transforms (Standard-mode optimiser building blocks) ------------
    def reordered(self, order: list[str]) -> "MasterResume":
        """
        Returns a copy with sections in the given order; anything unnamed
        keeps its relative position at the end. Never drops a section.
        """
        wanted = [name.strip().lower() for name in order]

        def rank(section: Section) -> tuple[int, int]:
            key = section.name.strip().lower()
            return ((wanted.index(key), 0) if key in wanted
                    else (len(wanted), self.sections.index(section)))

        return MasterResume(contact=self.contact,
                            sections=sorted(self.sections, key=rank))

    # --- serialisation ---------------------------------------------------
    def to_markdown(self) -> str:
        lines: list[str] = [f"# {self.contact.name}".rstrip()]
        if self.contact.headline:
            lines.append(self.contact.headline)
        details = self.contact.detail_line()
        if details:
            lines.append(details)

        for section in self.sections:
            lines.extend(["", f"## {section.name}"])
            if section.prose:
                lines.extend(["", section.prose])
            if section.items:
                lines.extend(["", ", ".join(section.items)])
            for entry in section.entries:
                lines.extend(["", f"### {entry.heading()}"])
                if entry.meta:
                    lines.append(entry.meta)
                lines.extend(f"- {bullet}" for bullet in entry.bullets)
        return "\n".join(lines).strip() + "\n"


# ======================================================
# PARSING
# ======================================================
def _parse_contact(block: list[str]) -> Contact:
    """Reads the name, headline, and contact details above the first section."""
    contact = Contact()
    lines = [line.strip() for line in block if line.strip()]
    if not lines:
        return contact

    contact.name = lines[0].lstrip("# ").strip()
    for line in lines[1:]:
        parts = [part.strip() for part in _CONTACT_SPLIT.split(line)
                 if part.strip()]
        looks_like_details = (len(parts) > 1 or _EMAIL.search(line)
                              or _PHONE.search(line))
        if not looks_like_details:
            if not contact.headline:
                contact.headline = line
            continue
        for part in parts:
            if _EMAIL.fullmatch(part) or (not contact.email
                                          and _EMAIL.search(part)):
                contact.email = _EMAIL.search(part).group()
            elif not contact.phone and _PHONE.fullmatch(part):
                contact.phone = part
            elif _LINK.fullmatch(part):
                contact.links.append(part)
            elif not contact.location:
                contact.location = part
            else:
                contact.links.append(part)
    return contact


def _parse_entry(heading: str, body: list[str]) -> Entry:
    """Reads one '### Title — Employer' block."""
    pieces = _ENTRY_SPLIT.split(heading, maxsplit=1)
    entry = Entry(title=pieces[0].strip(),
                  organisation=pieces[1].strip() if len(pieces) > 1 else "")
    for line in body:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "•")):
            entry.bullets.append(stripped.lstrip("-*• ").strip())
        elif not entry.meta and not entry.bullets:
            entry.meta = stripped
    return entry


def _parse_section(name: str, body: list[str]) -> Section:
    """Reads one '## Name' block into prose, a list, or dated entries."""
    section = Section(name=name.strip())

    # Split the body into the part before the first ### and the entries.
    preamble: list[str] = []
    entry_heading: str | None = None
    entry_body: list[str] = []
    for line in body:
        if line.startswith("### "):
            if entry_heading is not None:
                section.entries.append(_parse_entry(entry_heading, entry_body))
            entry_heading, entry_body = line[4:].strip(), []
        elif entry_heading is None:
            preamble.append(line)
        else:
            entry_body.append(line)
    if entry_heading is not None:
        section.entries.append(_parse_entry(entry_heading, entry_body))

    text = "\n".join(preamble).strip()
    if text:
        if section.kind == "list":
            section.items = [item.strip() for item in
                             re.split(r"[,\n]", text) if item.strip()]
        else:
            bullets = [line.strip().lstrip("-*• ").strip()
                       for line in text.splitlines()
                       if line.strip().startswith(("-", "*", "•"))]
            if bullets and section.kind != "prose":
                section.entries.append(Entry(bullets=bullets))
            else:
                section.prose = " ".join(
                    line.strip() for line in text.splitlines() if line.strip())
    return section


def parse_markdown(text: str) -> MasterResume:
    """
    Reads a master resume Markdown document into the model.
    HTML comments are stripped first, so review banners and the user's own
    notes never leak into the resume.
    """
    text = _HTML_COMMENT.sub("", text or "")
    header: list[str] = []
    sections: list[Section] = []
    current_name: str | None = None
    current_body: list[str] = []

    for line in (text or "").splitlines():
        if line.startswith("## "):
            if current_name is not None:
                sections.append(_parse_section(current_name, current_body))
            current_name, current_body = line[3:].strip(), []
        elif current_name is None:
            header.append(line)
        else:
            current_body.append(line)
    if current_name is not None:
        sections.append(_parse_section(current_name, current_body))

    return MasterResume(contact=_parse_contact(header), sections=sections)


# ======================================================
# PUBLIC API — FILES
# ======================================================
def load(path: str) -> MasterResume:
    """Reads a master resume from disk."""
    with open(path, "r", encoding="utf-8") as handle:
        return parse_markdown(handle.read())


def save(resume: MasterResume, path: str) -> None:
    """Writes a master resume to disk as Markdown."""
    import os
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(resume.to_markdown())

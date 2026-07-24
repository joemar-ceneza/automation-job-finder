"""
summary.py
Standard-mode job summary: condense a long advertisement into the parts you
actually triage on — what you'd do, what they require, what's nice to have,
what they offer — plus rule-based red flags for the scams and traps that recur
in this market.

Entirely deterministic. Section extraction works because ads use predictable
headings ("Responsibilities", "Requirements", "Benefits"); when a heading is
absent the field is left empty rather than guessed at. Salary, work arrangement,
and required years are already parsed at scrape time, so this only has to pull
the prose sections and screen for red flags.

This is the Standard-mode half of the job-summary feature. AI mode takes this
output and adds prose, pros/cons, and subtler red flags — it never re-derives
these facts from the raw advertisement.
"""
import re
from dataclasses import dataclass, field

# Canonical section -> the headings that introduce it. Ads vary the wording, so
# each bucket matches several. Order matters: the first heading a line matches
# wins, so put the more specific patterns first within a bucket's alternation.
_HEADINGS = [
    ("responsibilities", re.compile(
        r"^\s*(key\s+)?(responsibilities|duties|what\s+you.?ll\s+do|"
        r"the\s+role|role\s+overview|job\s+summary|key\s+tasks|"
        r"scope\s+of\s+work|your\s+role|day[- ]to[- ]day)\b", re.IGNORECASE)),
    ("nice_to_have", re.compile(
        r"^\s*(nice[- ]to[- ]have|preferred\s+qualifications?|bonus\s+points?|"
        r"good\s+to\s+have|pluses|advantageous|a\s+plus)\b", re.IGNORECASE)),
    ("requirements", re.compile(
        r"^\s*(requirements|qualifications|what\s+we.?re\s+looking\s+for|"
        r"who\s+you\s+are|skills\s+(and|&)\s+experience|minimum\s+qualifications|"
        r"must[- ]haves?|you\s+(should\s+have|will\s+need|have))\b",
        re.IGNORECASE)),
    ("benefits", re.compile(
        r"^\s*(benefits|perks|what\s+we\s+offer|we\s+offer|why\s+join|"
        r"compensation\s+(and|&)\s+benefits|what.?s\s+in\s+it\s+for\s+you)\b",
        re.IGNORECASE)),
]
_BUCKETS = ("responsibilities", "requirements", "nice_to_have", "benefits")

# Leading bullet glyphs, or numbered-list markers like "1." / "2)", to strip
# from the front of an item. Only a digit that is followed by "." or ")" is a
# list marker — a bare leading digit ("3+ years") is content and must survive.
_BULLET_LEAD = re.compile(r"^\s*(?:[•‣◦⁃·▪●○\-\*–—]+\s*|\d+[.)]\s+)+")
# Split a run-on line that packs several bullets onto one line.
_INLINE_SPLIT = re.compile(r"\s*[•‣▪●]\s+|\s{2,}[-–]\s+")
# Cap per section so a summary stays a summary.
_MAX_ITEMS = 12


@dataclass
class JobSummary:
    """The scannable form of one advertisement."""
    job_key: str = ""
    title: str = ""
    company: str = ""
    work_arrangement: str = "Unstated"
    salary_text: str = ""
    required_years: int | None = None
    responsibilities: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def has_sections(self) -> bool:
        return bool(self.responsibilities or self.requirements
                    or self.nice_to_have or self.benefits)


# ======================================================
# SECTION EXTRACTION
# ======================================================
def _heading_bucket(line: str) -> tuple[str, str] | None:
    """
    If a line is a section heading, returns (bucket, inline_remainder) — the
    remainder being any content after a colon on the same line ("Requirements:
    3 years of Python"). Returns None for an ordinary line.
    """
    stripped = line.strip()
    # A heading is short and label-like; a 30-word sentence that happens to
    # contain "requirements" is not a heading.
    if not stripped or len(stripped.split()) > 8:
        return None
    for bucket, pattern in _HEADINGS:
        if pattern.match(stripped):
            remainder = ""
            if ":" in stripped:
                remainder = stripped.split(":", 1)[1].strip()
            return bucket, remainder
    return None


def _split_items(raw_lines: list[str]) -> list[str]:
    """Turns a section's captured lines into clean, de-duplicated bullet items."""
    items: list[str] = []
    seen: set[str] = set()
    for line in raw_lines:
        for part in _INLINE_SPLIT.split(line):
            item = _BULLET_LEAD.sub("", part).strip()
            if len(item) < 3:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return items[:_MAX_ITEMS]


def _extract_sections(body: str) -> dict[str, list[str]]:
    """
    Pulls the responsibilities/requirements/nice-to-have/benefits blocks out of
    an advertisement by its headings. A field stays empty when its heading is
    absent — no guessing.
    """
    raw: dict[str, list[str]] = {bucket: [] for bucket in _BUCKETS}
    current: str | None = None
    for line in (body or "").splitlines():
        heading = _heading_bucket(line)
        if heading:
            current, remainder = heading
            if remainder:
                raw[current].append(remainder)
            continue
        if current and line.strip():
            raw[current].append(line)
    return {bucket: _split_items(lines) for bucket, lines in raw.items()}


# ======================================================
# RED FLAGS
# ======================================================
def _required_years(job: dict) -> int | None:
    value = job.get("required_years")
    if isinstance(value, int):
        return value
    text = str(value or "")
    return int(text) if text.isdigit() else None


def _red_flags(job: dict, required_years: int | None) -> list[str]:
    """
    The obvious scams and traps, matched by rule. Deliberately conservative —
    a false accusation is worse than a missed one, so each pattern targets
    language that is specifically a warning sign, not merely unusual.
    """
    company = (job.get("company") or "").strip()
    salary_text = (job.get("salary") or "")
    body = job.get("description") or job.get("teaser") or ""
    text = f"{job.get('title', '')}\n{body}".lower()
    flags = []

    if re.search(r"\b(registration|placement|processing|training|application)\s+"
                 r"fee\b|pay\s+(a\s+)?fee|send\s+(us\s+)?money|"
                 r"deposit\s+required", text):
        flags.append("Asks applicants for a fee or payment — a legitimate "
                     "employer never charges you to apply.")
    if re.search(r"unlimited\s+(earning|income|potential)|be\s+your\s+own\s+boss|"
                 r"earn\s+up\s+to.{0,25}(daily|weekly|per\s+day)", text):
        flags.append("“Unlimited earning” / “be your own boss” language — "
                     "typical of MLM or commission-only schemes.")
    if (re.search(r"\b(crypto|cryptocurrency|forex|binary\s+option|"
                  r"trading\s+platform)\b", text)
            and re.search(r"\b(recruit|agent|investor|referral|downline)\b",
                          text)):
        flags.append("Crypto/forex recruitment signals — treat with caution.")
    if not company and re.search(r"[\w.+-]+@(gmail|yahoo|hotmail|outlook|aol|"
                                 r"proton)\b", text):
        flags.append("No company name and a personal email address — verify "
                     "who is actually hiring.")
    if (required_years and required_years >= 5
            and re.search(r"\bcompetitive\b", (salary_text or text).lower())):
        flags.append(f"Senior role ({required_years}+ yrs) but salary is only "
                     "“competitive” — ask for a range before investing time.")
    return flags


# ======================================================
# RENDERING
# ======================================================
def _describe(summary: JobSummary) -> list[str]:
    """Renders the summary as lines for the terminal."""
    lines = [f"{summary.title} @ {summary.company or 'unknown company'}"]
    facts = [summary.work_arrangement]
    if summary.salary_text:
        facts.append(summary.salary_text)
    if summary.required_years:
        facts.append(f"{summary.required_years}+ yrs")
    lines.append(" · ".join(facts))

    labels = [("responsibilities", "What you'd do"),
              ("requirements", "What they require"),
              ("nice_to_have", "Nice to have"),
              ("benefits", "What they offer")]
    for attribute, heading in labels:
        items = getattr(summary, attribute)
        if items:
            lines.append("")
            lines.append(f"{heading}:")
            lines.extend(f"  • {item}" for item in items)

    if not summary.has_sections():
        lines.append("")
        lines.append("This advert has no clear sections to extract — read the "
                     "full posting.")

    if summary.red_flags:
        lines.append("")
        lines.append("⚠ Red flags:")
        lines.extend(f"  • {flag}" for flag in summary.red_flags)
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def summarise(job: dict) -> JobSummary:
    """
    Builds a scannable summary of one stored job row. Deterministic: every field
    is extracted from the advertisement or already-parsed columns, never
    invented. Missing headings leave their field empty.
    """
    required_years = _required_years(job)
    sections = _extract_sections(job.get("description") or job.get("teaser")
                                 or "")
    summary = JobSummary(
        job_key=job.get("job_key", ""),
        title=job.get("title", "") or "the role",
        company=(job.get("company") or "").strip(),
        work_arrangement=(job.get("work_arrangement") or "").strip()
        or "Unstated",
        salary_text=(job.get("salary") or "").strip(),
        required_years=required_years,
        responsibilities=sections["responsibilities"],
        requirements=sections["requirements"],
        nice_to_have=sections["nice_to_have"],
        benefits=sections["benefits"],
        red_flags=_red_flags(job, required_years),
    )
    summary.lines = _describe(summary)
    return summary

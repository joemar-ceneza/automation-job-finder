"""
dedupe.py
Flags the same job advertised more than once, so it is reviewed once.

Scope is deliberately narrow. Duplicates are only claimed when the same
employer posts the same role, because that is the only signal the scraped data
actually supports:

- OnlineJobs.ph publishes no employer name at all, so a cross-site match
  between it and JobStreet has nothing to anchor on.
- Title alone is worthless as a key — "Full Stack Developer" appeared at
  twelve different JobStreet employers in a single 232-job sample.
- JobStreet's "Private Advertiser" is a placeholder shared by many unrelated
  employers, so it is never treated as a company.

Duplicates are flagged, never deleted: the scraper may be wrong, and a
recruiter reposting a role can mean it is still open.
"""
import logging
import re
from collections import defaultdict

import config

# Dropped from the title before comparison — seniority and posting noise vary
# between reposts of the same role.
_TITLE_NOISE = re.compile(
    r"\b(senior|sr|junior|jr|mid|midweight|entry|level|"
    r"full[- ]?time|part[- ]?time|permanent|contract|freelance|"
    r"remote|onsite|hybrid|urgent|hiring|now|asap|wfh|"
    r"i|ii|iii|iv)\b", re.IGNORECASE)

_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_TITLE_CHARS = re.compile(r"[^a-z0-9+#. ]+")
_WHITESPACE = re.compile(r"\s+")


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _normalise_title(title: str) -> str:
    """Reduces a title to its role, so reposts with different dressing match."""
    text = (title or "").lower()
    text = _PARENTHETICAL.sub(" ", text)
    text = _NON_TITLE_CHARS.sub(" ", text)
    text = _TITLE_NOISE.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _normalise_company(company: str) -> str:
    """
    Reduces a company name to a comparable form, or "" when the name carries
    no identity (blank, or a placeholder like "Private Advertiser").
    """
    text = (company or "").lower().strip()
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\b(inc|incorporated|corp|corporation|ltd|limited|"
                  r"co|company|llc|philippines|phils|ph)\b", " ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text or text in config.PLACEHOLDER_COMPANIES:
        return ""
    return text


def _canonical(group: list[dict]) -> dict:
    """The posting the others defer to — the one seen first."""
    return min(group, key=lambda job: (job.get("first_seen") or "",
                                       job.get("job_key") or ""))


# ======================================================
# PUBLIC API
# ======================================================
def find_duplicates(jobs: list[dict]) -> dict[str, str]:
    """
    Maps each duplicate job_key to the job_key it duplicates.
    Only same-employer, same-role repostings are reported.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    skipped_no_company = 0

    for job in jobs:
        company = _normalise_company(job.get("company", ""))
        if not company:
            skipped_no_company += 1
            continue
        title = _normalise_title(job.get("title", ""))
        if title:
            grouped[(title, company)].append(job)

    duplicates: dict[str, str] = {}
    for group in grouped.values():
        if len(group) < 2:
            continue
        keeper = _canonical(group)
        for job in group:
            if job["job_key"] != keeper["job_key"]:
                duplicates[job["job_key"]] = keeper["job_key"]

    if skipped_no_company:
        logging.info("Duplicate check skipped %d listing(s) with no usable "
                     "employer name — nothing reliable to match on.",
                     skipped_no_company)
    logging.info("Found %d duplicate listing(s) across %d job(s).",
                 len(duplicates), len(jobs))
    return duplicates

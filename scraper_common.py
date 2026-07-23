"""
scraper_common.py
Shared building blocks for the per-site scraper modules
(scraper_jobstreet.py, scraper_onlinejobs.py):
the JobListing dataclass, dedupe-key builder, relative-date parsing,
and debug HTML / error screenshot snapshots.
"""
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import config


class AdGoneError(Exception):
    """Raised when a job ad was removed/expired after appearing in search."""


@dataclass
class JobListing:
    job_key: str
    title: str
    company: str
    location: str
    teaser: str
    url: str
    source: str = ""           # which site this came from (jobstreet/onlinejobs)
    salary: str = ""
    description: str = ""
    listing_date: str = ""     # ISO date derived from the site's posted date
    search_keyword: str = ""   # which search term found this listing


# ======================================================
# DEDUPE KEY
# ======================================================
def make_job_key(source: str, job_id: str, title: str, company: str) -> str:
    """
    Stable site-prefixed dedupe key: the site's numeric/hash job id when
    available, otherwise normalized title+company.
    """
    if job_id:
        return f"{source}:id:{job_id}"
    title_norm = re.sub(r"\s+", " ", title.lower()).strip()
    company_norm = re.sub(r"\s+", " ", company.lower()).strip()
    return f"{source}:tc:{title_norm}|{company_norm}"


# ======================================================
# DATE PARSING
# ======================================================
_RELATIVE_DATE_PATTERN = re.compile(
    r"(\d+)\s*(m|h|d|minute|hour|day|week|month)s?\b", re.IGNORECASE)


def parse_relative_date(raw_text: str) -> str:
    """
    Converts a relative age ("11h ago", "3d ago", "2 days ago") to an
    absolute ISO date so it stays meaningful in the database.
    Returns "" when the text doesn't match.
    """
    if re.search(r"just posted|today", raw_text, re.IGNORECASE):
        return datetime.now().date().isoformat()
    match = _RELATIVE_DATE_PATTERN.search(raw_text)
    if not match:
        return ""
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit in ("m", "minute"):
        delta = timedelta(minutes=amount)
    elif unit in ("h", "hour"):
        delta = timedelta(hours=amount)
    elif unit == "week":
        delta = timedelta(weeks=amount)
    elif unit == "month":
        delta = timedelta(days=amount * 30)
    else:
        delta = timedelta(days=amount)
    return (datetime.now() - delta).date().isoformat()


# ======================================================
# BLOCKLISTS
# ======================================================
def _blocked_company(listing: JobListing, blocklist: list[str]) -> str | None:
    """The blocklisted company name this listing matches, if any."""
    company_lower = (listing.company or "").lower()
    if not company_lower:
        return None  # OnlineJobs.ph hides employers — nothing to match on
    return next((name for name in blocklist if name in company_lower), None)


def _blocked_title_keyword(listing: JobListing,
                           keywords: list[str]) -> str | None:
    """
    The blocklisted keyword this title contains, if any. Matched as a whole
    word so "lead" does not block "Leadership" and "manager" does not block
    "Management Trainee".
    """
    title_lower = (listing.title or "").lower()
    for keyword in keywords:
        # (?<!\w)/(?!\w) rather than \b: a keyword that starts or ends with a
        # non-word character (".net", "c++") has no word boundary there, so \b
        # would never match it.
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", title_lower):
            return keyword
    return None


def filter_blocklisted(listings: list[JobListing]) -> list[JobListing]:
    """
    Drops listings whose company is in config.BLOCKLISTED_COMPANIES or whose
    title contains a config.BLOCKLISTED_TITLE_KEYWORDS entry. Listings with no
    company name are only ever filtered on their title.
    """
    companies = [name.lower() for name in config.BLOCKLISTED_COMPANIES]
    keywords = [word.lower() for word in config.BLOCKLISTED_TITLE_KEYWORDS]
    if not companies and not keywords:
        return listings

    kept, by_company, by_title = [], 0, 0
    for listing in listings:
        company = _blocked_company(listing, companies)
        if company:
            logging.debug("Blocklisted company '%s' — skipping '%s'",
                          company, listing.title)
            by_company += 1
            continue
        keyword = _blocked_title_keyword(listing, keywords)
        if keyword:
            logging.debug("Blocklisted title keyword '%s' — skipping '%s'",
                          keyword, listing.title)
            by_title += 1
            continue
        kept.append(listing)

    if by_company or by_title:
        logging.info("Blocklist removed %d listing(s): %d by company, "
                     "%d by title keyword.", by_company + by_title,
                     by_company, by_title)
    return kept


# ======================================================
# DEBUG SNAPSHOTS
# ======================================================
def save_debug_html(page, label: str) -> str:
    """Saves the current page HTML to logs/ for selector troubleshooting."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(config.LOGS_DIR, f"debug_{label}_{timestamp}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    logging.info("Saved page HTML to %s", path)
    return path


def save_error_screenshot(page, label: str) -> None:
    """Saves a screenshot to logs/screenshots/ after a scraping failure."""
    try:
        os.makedirs(config.SCREENSHOTS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(config.SCREENSHOTS_DIR, f"error_{label}_{timestamp}.png")
        page.screenshot(path=path)
        logging.info("Saved error screenshot to %s", path)
    except Exception as e:
        logging.warning("Could not save error screenshot: %s", e)

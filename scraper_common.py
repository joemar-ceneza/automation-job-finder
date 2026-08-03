"""
scraper_common.py
Shared building blocks for the per-site scraper modules: the JobListing
dataclass, dedupe-key builder, relative-date parsing, anti-bot detection,
the shared detail-page fetcher, and debug HTML / error screenshot snapshots.
"""
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import config
import utils


class AdGoneError(Exception):
    """Raised when a job ad was removed/expired after appearing in search."""


class AntiBotBlockedError(Exception):
    """
    Raised when a site serves a bot challenge instead of content. Distinct from
    a markup change: no selector edit fixes it, and retrying only burns time,
    so it is given up on immediately and reported as what it is.
    """


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
# FIELD EXTRACTION
# ======================================================
def text_from(element, selector: str | None) -> str:
    """
    Text of the first match, or "" when the selector is absent or misses.

    A selector may be None on purpose: some sites give several fields the same
    utility class, and there is no way to tell them apart on a search card.
    Leaving the field empty is honest; pointing it at a colliding selector
    silently fills it with the wrong text.
    """
    if not selector or element is None:
        return ""
    found = element.query_selector(selector)
    return found.inner_text().strip() if found else ""


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
# ANTI-BOT DETECTION
# ======================================================
# Phrases that appear on a challenge/verification interstitial rather than on a
# results page. Kept here so every scraper reports a block the same way.
_BLOCK_MARKERS = (
    "just a moment", "verify you are human", "are you a robot",
    "additional verification", "unusual traffic", "cf-challenge",
    "px-captcha", "captcha-delivery", "access denied",
)


def is_blocked(page) -> bool:
    """
    True when the page is a bot challenge rather than content.

    This matters because the symptom is identical to a markup change — zero
    cards extracted — but the cause and the fix are completely different.
    Reporting "the site may have changed markup" for a CAPTCHA sends you to
    inspect HTML that is perfectly fine.
    """
    try:
        haystack = f"{page.title()} {page.content()[:4000]}".lower()
    except Exception:                       # page torn down mid-check
        return False
    return any(marker in haystack for marker in _BLOCK_MARKERS)


# ======================================================
# JOB DETAIL PAGES
# ======================================================
def ad_is_gone(page, response, gone_marker: str) -> bool:
    """True when the ad was removed/expired (404, takedown page, or marker)."""
    if response is not None and response.status in (404, 410):
        return True
    try:
        if "page not found" in (page.title() or "").lower():
            return True
        return gone_marker in (page.content() or "").lower()
    except Exception:
        return False


def fetch_job_details(context, selectors: dict, url: str,
                      gone_marker: str) -> tuple[str, str]:
    """
    Opens a job's detail page in a fresh tab and returns
    (full_description, salary). Salary is "" when the ad doesn't state one.
    Raises AdGoneError when the ad was removed after appearing in search.
    """
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded",
                             timeout=config.PAGE_LOAD_TIMEOUT_MS)
        if ad_is_gone(page, response, gone_marker):
            raise AdGoneError(f"job ad removed: {url}")
        try:
            page.wait_for_selector(selectors["job_detail_description"],
                                   timeout=config.DETAIL_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # A takedown page can render after domcontentloaded — recheck before
            # blaming the selector.
            if ad_is_gone(page, response, gone_marker):
                raise AdGoneError(f"job ad removed: {url}")
            raise

        detail_el = page.query_selector(selectors["job_detail_description"])
        salary_el = (page.query_selector(selectors["job_detail_salary"])
                     if selectors.get("job_detail_salary") else None)
        return (detail_el.inner_text().strip() if detail_el else "",
                salary_el.inner_text().strip() if salary_el else "")
    finally:
        page.close()


def fetch_full_descriptions(source: str, selectors: dict, context,
                            listings: list[JobListing], delay_seconds: float,
                            gone_marker: str = "no longer available") -> None:
    """
    Visits each job's detail page (rate limited) and fills in the description.

    Two failure modes are handled differently on purpose. An ad that has been
    taken down is normal and per-job — keep the search teaser and move on. A
    selector timeout is neither: it means `job_detail_description` does not
    match this site's markup, which cannot come right on a retry and will not
    come right on the next job either. So timeouts are given up on immediately
    rather than retried, and a run of them stops the whole pass.

    Without that, a single wrong selector costs RETRY_ATTEMPTS x
    DETAIL_WAIT_TIMEOUT_MS plus backoff on every listing — around 39 seconds
    each with the shipped defaults, which is an hour of dead waiting on a
    hundred jobs, and the only clue is a wall of identical timeout warnings.
    """
    logging.info("[%s] Fetching full descriptions for %d jobs "
                 "(one request per %.1fs)...", source, len(listings),
                 delay_seconds)
    fetched = gone = failed = 0
    consecutive_timeouts = 0

    for index, listing in enumerate(listings, start=1):
        try:
            description, salary = utils.retry(
                lambda: fetch_job_details(context, selectors, listing.url,
                                          gone_marker),
                retries=config.RETRY_ATTEMPTS,
                delay=config.RETRY_DELAY_SECONDS,
                backoff=config.RETRY_BACKOFF,
                give_up_on=(AdGoneError, PlaywrightTimeoutError,
                            AntiBotBlockedError),
            )
            listing.description = description
            if salary and not listing.salary:
                listing.salary = salary
            fetched += 1
            consecutive_timeouts = 0
        except AdGoneError:
            gone += 1
            consecutive_timeouts = 0
            logging.info("[%s] '%s' is no longer advertised — keeping the "
                         "search-card teaser.", source, listing.title)
        except PlaywrightTimeoutError:
            failed += 1
            consecutive_timeouts += 1
            if consecutive_timeouts >= config.DETAIL_FAILURE_LIMIT:
                logging.error(
                    "[%s] %d detail pages in a row timed out waiting for %r. "
                    "That selector does not match this site's markup — no "
                    "retry can fix it. Skipping full descriptions for the "
                    "remaining %d job(s); their search-card teasers are kept. "
                    "Fix SELECTORS['%s']['job_detail_description'] in "
                    "config.py, or run without --full-desc.",
                    source, consecutive_timeouts,
                    selectors.get("job_detail_description"),
                    len(listings) - index, source)
                break
            logging.warning("[%s] Timed out loading the detail page for '%s'.",
                            source, listing.title)
        except Exception as error:
            failed += 1
            consecutive_timeouts = 0
            logging.error("[%s] Could not fetch description for '%s' (%s): %s",
                          source, listing.title, listing.url, error)
        if index < len(listings):
            time.sleep(delay_seconds)      # be polite, avoid rate limits

    logging.info("[%s] Full descriptions fetched: %d/%d (%d no longer "
                 "advertised, %d failed)", source, fetched, len(listings),
                 gone, failed)


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

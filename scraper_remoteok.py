"""
scraper_remoteok.py
Fetches remote job listings from the RemoteOK public JSON API
(https://remoteok.com/api) — no browser, no selectors to break.

Notes:
- The API returns their full current listing feed in one request; keywords
  are matched locally against position, tags, and description.
- Salaries are USD/year estimates; kept as raw text and not normalized
  into the peso salary_min/salary_max columns.
- Full descriptions come with the API response, so --full-desc is free.
- RemoteOK asks for attribution when data is shared publicly; this tool
  only uses it for personal job searching.
"""
import logging

import requests

import config
import utils
from scraper_common import JobListing, html_to_text, make_job_key

SOURCE = "remoteok"


# ======================================================
# API FETCH
# ======================================================
def _fetch_all_listings() -> list[dict]:
    """Downloads the RemoteOK feed (first element is a legal notice)."""
    def _get():
        response = requests.get(
            config.REMOTEOK_API_URL,
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    payload = utils.retry(_get, retries=config.RETRY_ATTEMPTS,
                          delay=config.RETRY_DELAY_SECONDS,
                          backoff=config.RETRY_BACKOFF)
    return [item for item in payload if isinstance(item, dict) and item.get("id")]


def _format_salary(item: dict) -> str:
    """Formats RemoteOK's USD/year salary estimate as readable raw text."""
    salary_min = item.get("salary_min") or 0
    salary_max = item.get("salary_max") or 0
    if not salary_min and not salary_max:
        return ""
    return f"${salary_min:,.0f} - ${salary_max:,.0f}/year (USD)"


def _matches_keyword(item: dict, keyword_lower: str) -> bool:
    """True when every word of the keyword appears in the job's text."""
    haystack = " ".join([
        item.get("position") or "",
        " ".join(item.get("tags") or []),
        item.get("description") or "",
    ]).lower()
    return all(word in haystack for word in keyword_lower.split())


def _to_listing(item: dict, search_keyword: str) -> JobListing:
    """Converts one RemoteOK API item to a JobListing."""
    return JobListing(
        job_key=make_job_key(SOURCE, str(item["id"]),
                             item.get("position") or "", item.get("company") or ""),
        title=(item.get("position") or "").strip(),
        company=(item.get("company") or "").strip(),
        location=(item.get("location") or "Remote").strip() or "Remote",
        teaser=html_to_text(item.get("description") or "")[:300],
        url=item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}",
        source=SOURCE,
        salary=_format_salary(item),
        description=html_to_text(item.get("description") or ""),
        listing_date=(item.get("date") or "")[:10],
        search_keyword=search_keyword,
    )


# ======================================================
# PUBLIC ENTRY POINT
# ======================================================
def run_scraper(keywords: list[str] | str, max_pages: int = config.DEFAULT_PAGES,
                delay_seconds: float = config.DEFAULT_DELAY_SECONDS,
                debug: bool = False, fetch_details: bool = False,
                location: str = "") -> list[JobListing]:
    """
    Fetches the RemoteOK feed once and returns listings matching any of the
    keywords, deduped by job id. max_pages caps results per keyword
    (30/page-equivalent); location is ignored (remote-only); full
    descriptions are always included.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    if location:
        logging.info("[remoteok] Location filter ignored — remote-only site.")

    try:
        items = _fetch_all_listings()
    except Exception as e:
        logging.error("[remoteok] API request failed: %s", e)
        return []
    logging.info("[remoteok] Feed contains %d listings.", len(items))

    per_keyword_cap = max_pages * 30
    unique_listings: dict[str, JobListing] = {}
    for keyword in keywords:
        keyword_lower = keyword.lower()
        matched = 0
        for item in items:
            if matched >= per_keyword_cap:
                break
            if not _matches_keyword(item, keyword_lower):
                continue
            listing = _to_listing(item, keyword)
            if listing.job_key not in unique_listings:
                unique_listings[listing.job_key] = listing
                matched += 1
        logging.info("[remoteok] Keyword '%s': %d matches (%d unique so far)",
                     keyword, matched, len(unique_listings))

    return list(unique_listings.values())

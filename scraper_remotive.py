"""
scraper_remotive.py
Fetches remote job listings from the Remotive public JSON API
(https://remotive.com/api/remote-jobs) — no browser, no selectors to break.

Notes:
- The API supports server-side keyword search; one request per keyword.
- candidate_required_location becomes the location column — check it, as
  some listings are restricted to specific regions.
- Salaries are free-text (often USD); kept raw, not normalized into the
  peso salary_min/salary_max columns unless stated in PHP.
- Full descriptions come with the API response, so --full-desc is free.
"""
import logging
import time

import requests

import config
import utils
from scraper_common import JobListing, html_to_text, make_job_key

SOURCE = "remotive"


# ======================================================
# API FETCH
# ======================================================
def _fetch_keyword(keyword: str, limit: int) -> list[dict]:
    """Queries the Remotive API for one keyword."""
    def _get():
        response = requests.get(
            config.REMOTIVE_API_URL,
            params={"search": keyword, "limit": limit},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    payload = utils.retry(_get, retries=config.RETRY_ATTEMPTS,
                          delay=config.RETRY_DELAY_SECONDS,
                          backoff=config.RETRY_BACKOFF)
    return payload.get("jobs", [])


def _to_listing(item: dict, search_keyword: str) -> JobListing:
    """Converts one Remotive API item to a JobListing."""
    description = html_to_text(item.get("description") or "")
    return JobListing(
        job_key=make_job_key(SOURCE, str(item.get("id") or ""),
                             item.get("title") or "",
                             item.get("company_name") or ""),
        title=(item.get("title") or "").strip(),
        company=(item.get("company_name") or "").strip(),
        location=(item.get("candidate_required_location") or "Remote").strip(),
        teaser=description[:300],
        url=(item.get("url") or "").strip(),
        source=SOURCE,
        salary=(item.get("salary") or "").strip(),
        description=description,
        listing_date=(item.get("publication_date") or "")[:10],
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
    Queries the Remotive API for each keyword (rate limited between
    requests) and returns listings deduped by job id. max_pages caps
    results per keyword (30/page-equivalent); location is ignored
    (remote-only); full descriptions are always included.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    if location:
        logging.info("[remotive] Location filter ignored — remote-only site "
                     "(check the location column for region restrictions).")

    per_keyword_cap = max_pages * 30
    unique_listings: dict[str, JobListing] = {}
    for index, keyword in enumerate(keywords):
        try:
            items = _fetch_keyword(keyword, per_keyword_cap)
        except Exception as e:
            logging.error("[remotive] API request failed for '%s': %s", keyword, e)
            continue
        added = 0
        for item in items:
            listing = _to_listing(item, keyword)
            if listing.job_key not in unique_listings:
                unique_listings[listing.job_key] = listing
                added += 1
        logging.info("[remotive] Keyword '%s': %d listings (%d unique so far)",
                     keyword, added, len(unique_listings))
        if index < len(keywords) - 1:
            time.sleep(delay_seconds)  # be polite between API calls

    return list(unique_listings.values())

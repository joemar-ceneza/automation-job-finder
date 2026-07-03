"""
scraper_onlinejobs.py
Scrapes job listings from OnlineJobs.ph (remote jobs for Filipino workers)
for given search terms using Playwright.

Notes:
- All OnlineJobs.ph listings are work-from-home by design, so the location
  field is set to "Work from Home".
- Salaries are usually stated in USD (e.g. "1200$/month"); they are kept as
  raw text and NOT normalized into the peso salary_min/salary_max columns.
- The employer name is not shown on search cards, so company is blank.
- Location filters don't apply here (remote-only site) and are ignored.
"""
import logging
import re
import time
import urllib.parse

from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (JobListing, make_job_key, save_debug_html,
                            save_error_screenshot)

SOURCE = "onlinejobs"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"-(\d+)/?$")
_RESULTS_PER_PAGE = 30


# ======================================================
# URL HELPERS
# ======================================================
def _build_search_url(keyword: str, page_num: int) -> str:
    """
    Builds the OnlineJobs.ph search URL. Pagination is offset-based:
    page 1 = /jobsearch, page 2 = /jobsearch/30, page 3 = /jobsearch/60.
    """
    query = urllib.parse.quote_plus(keyword.strip())
    offset = (page_num - 1) * _RESULTS_PER_PAGE
    path = "/jobseekers/jobsearch" + (f"/{offset}" if offset else "")
    return f"{config.ONLINEJOBS_BASE_URL}{path}?jobkeyword={query}"


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from an OnlineJobs.ph search-result card."""
    link_el = card.query_selector(_SELECTORS["job_link"])
    title_el = card.query_selector(_SELECTORS["job_title"])
    if not link_el or not title_el:
        return None

    # The h4 holds the title plus a job-type badge ("Full Time") — strip it.
    title = title_el.inner_text().strip()
    badge_el = card.query_selector(_SELECTORS["job_title_badge"])
    if badge_el:
        title = title.replace(badge_el.inner_text().strip(), "").strip()

    href = link_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.ONLINEJOBS_BASE_URL + href

    teaser_el = card.query_selector(_SELECTORS["job_teaser"])
    salary_el = card.query_selector(_SELECTORS["job_salary"])
    date_el = card.query_selector(_SELECTORS["job_listing_date"])
    teaser = teaser_el.inner_text().strip() if teaser_el else ""
    teaser = re.sub(r"\s*See More\s*$", "", teaser)

    # data-temp holds an absolute timestamp like "2026-07-03 12:17:53"
    listing_date = ""
    if date_el:
        listing_date = (date_el.get_attribute("data-temp") or "")[:10]

    id_match = _JOB_ID_PATTERN.search(urllib.parse.urlparse(job_url).path)
    return JobListing(
        job_key=make_job_key(SOURCE, id_match.group(1) if id_match else "",
                             title, ""),
        title=title,
        company="",  # employer name isn't shown on search cards
        location="Work from Home",
        teaser=teaser,
        url=job_url,
        source=SOURCE,
        salary=salary_el.inner_text().strip() if salary_el else "",
        listing_date=listing_date,
        search_keyword=search_keyword,
    )


def _scrape_search_page(page, keyword: str, page_num: int,
                        debug: bool) -> list[JobListing]:
    """Loads one search-result page (with retries) and extracts its listings."""
    url = _build_search_url(keyword, page_num)
    logging.info("[onlinejobs] Fetching search page %d: %s", page_num, url)

    utils.retry(
        lambda: page.goto(url, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT_MS),
        retries=config.RETRY_ATTEMPTS,
        delay=config.RETRY_DELAY_SECONDS,
        backoff=config.RETRY_BACKOFF,
    )
    page.wait_for_timeout(config.RENDER_WAIT_MS)

    if debug:
        save_debug_html(page, f"onlinejobs_page{page_num}")

    cards = page.query_selector_all(_SELECTORS["job_card"])
    listings = []
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing:
            listings.append(listing)

    if not listings:
        html_path = save_debug_html(page, f"onlinejobs_no_results_page{page_num}")
        logging.warning(
            "[onlinejobs] 0 listings extracted from %s — the site may have "
            "changed markup. Inspect %s and update SELECTORS in config.py.",
            url, html_path)

    return listings


# ======================================================
# JOB DETAIL PAGES
# ======================================================
def _fetch_job_details(context, url: str) -> str:
    """Opens a job's detail page in a fresh tab and returns the description."""
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=config.PAGE_LOAD_TIMEOUT_MS)
        page.wait_for_selector(_SELECTORS["job_detail_description"],
                               timeout=config.DETAIL_WAIT_TIMEOUT_MS)
        detail_el = page.query_selector(_SELECTORS["job_detail_description"])
        return detail_el.inner_text().strip() if detail_el else ""
    finally:
        page.close()


def _fetch_full_descriptions(context, listings: list[JobListing],
                             delay_seconds: float) -> None:
    """Visits each job's detail page (rate limited) and fills in description."""
    logging.info("[onlinejobs] Fetching full descriptions for %d jobs "
                 "(one request per %.1fs)...", len(listings), delay_seconds)
    fetched = 0
    for index, listing in enumerate(listings, start=1):
        try:
            listing.description = utils.retry(
                lambda: _fetch_job_details(context, listing.url),
                retries=config.RETRY_ATTEMPTS,
                delay=config.RETRY_DELAY_SECONDS,
                backoff=config.RETRY_BACKOFF,
            )
            fetched += 1
        except Exception as e:
            logging.error("[onlinejobs] Could not fetch description for '%s' (%s): %s",
                          listing.title, listing.url, e)
        if index < len(listings):
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    logging.info("[onlinejobs] Full descriptions fetched: %d/%d",
                 fetched, len(listings))


def _scrape_keyword(page, keyword: str, max_pages: int, delay_seconds: float,
                    debug: bool,
                    unique_listings: dict[str, JobListing]) -> int:
    """
    Scrapes all result pages for one keyword into unique_listings.
    Returns the number of duplicates skipped.
    """
    duplicates = 0
    for page_num in range(1, max_pages + 1):
        try:
            listings = _scrape_search_page(page, keyword, page_num, debug)
        except Exception as e:
            logging.error("[onlinejobs] Failed to scrape search page %d: %s",
                          page_num, e)
            save_error_screenshot(page, f"onlinejobs_search_page{page_num}")
            break

        if not listings:
            logging.warning("[onlinejobs] No listings on page %d, stopping pagination.",
                            page_num)
            break

        for listing in listings:
            if listing.job_key in unique_listings:
                duplicates += 1
            else:
                unique_listings[listing.job_key] = listing
        logging.info("[onlinejobs] Page %d: %d listings (%d unique so far)",
                     page_num, len(listings), len(unique_listings))

        if page_num < max_pages:
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    return duplicates


# ======================================================
# PUBLIC ENTRY POINT
# ======================================================
def run_scraper(keywords: list[str] | str, max_pages: int = config.DEFAULT_PAGES,
                delay_seconds: float = config.DEFAULT_DELAY_SECONDS,
                debug: bool = False, fetch_details: bool = False,
                location: str = "") -> list[JobListing]:
    """
    Scrapes OnlineJobs.ph search results for one or more keywords (all in a
    single browser session) and dedupes listings by job_key.
    Owns the full browser lifecycle. Location is ignored (remote-only site).
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    if location:
        logging.info("[onlinejobs] Location filter ignored — all listings "
                     "are work-from-home.")

    unique_listings: dict[str, JobListing] = {}
    duplicates = 0
    browser = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=config.HEADLESS and not debug)
            context = browser.new_context(user_agent=config.USER_AGENT)
            page = context.new_page()

            for index, keyword in enumerate(keywords):
                logging.info("[onlinejobs] Searching keyword %d/%d: '%s'",
                             index + 1, len(keywords), keyword)
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)

            if duplicates:
                logging.info("[onlinejobs] Skipped %d duplicate listings "
                             "across pages/keywords.", duplicates)

            if fetch_details and unique_listings:
                _fetch_full_descriptions(context, list(unique_listings.values()),
                                         delay_seconds)
        finally:
            if browser:
                browser.close()
                logging.info("[onlinejobs] Browser closed cleanly.")

    return list(unique_listings.values())

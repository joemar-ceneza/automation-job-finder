"""
scraper_indeed.py
Scrapes job listings from Indeed Philippines (ph.indeed.com) for given
search terms using Playwright.

IMPORTANT (please read):
- Indeed sits behind aggressive Cloudflare anti-bot protection. Headless
  browsers get challenged or blocked intermittently. When that happens this
  scraper detects it, saves the page HTML for evidence, logs a clear
  warning, and returns whatever it managed to collect — the other sites'
  results are unaffected.
- Search cards don't show a posting date, so listing_date stays empty.
- Personal/non-commercial use only; keep request volume low.
"""
import logging
import time
import urllib.parse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (JobListing, make_job_key, save_debug_html,
                            save_error_screenshot)

SOURCE = "indeed"
_SELECTORS = config.SELECTORS[SOURCE]
_RESULTS_PER_PAGE = 10  # Indeed's &start= offset step

# Phrases that identify a Cloudflare/anti-bot interstitial. Title markers
# can be loose; body markers must be phrases that can't plausibly appear
# in a real job ad, since we scan the page content for them.
_TITLE_BLOCK_MARKERS = ("just a moment", "verify you are human",
                        "additional verification", "checking your browser",
                        "security check")
_BODY_BLOCK_MARKERS = ("verify you are human", "checking your browser",
                       "review the security of your connection",
                       "cf-turnstile", "additional verification required")


class BlockedError(Exception):
    """Raised when Indeed serves an anti-bot verification page."""


# ======================================================
# URL / BLOCK DETECTION HELPERS
# ======================================================
def _build_search_url(keyword: str, page_num: int, location: str = "") -> str:
    """Builds the Indeed PH search URL (offset-based pagination, 10/page)."""
    params = {"q": keyword.strip()}
    if location.strip():
        params["l"] = location.strip()
    if page_num > 1:
        params["start"] = (page_num - 1) * _RESULTS_PER_PAGE
    return f"{config.INDEED_BASE_URL}/jobs?{urllib.parse.urlencode(params)}"


def _is_blocked(page) -> bool:
    """True when the page is a Cloudflare/anti-bot challenge, not results."""
    title = (page.title() or "").lower()
    if any(marker in title for marker in _TITLE_BLOCK_MARKERS):
        return True
    content = (page.content() or "").lower()
    return any(marker in content for marker in _BODY_BLOCK_MARKERS)


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from an Indeed search-result card."""
    title_el = card.query_selector(_SELECTORS["job_title"])
    link_el = card.query_selector(_SELECTORS["job_title_link"])
    if not title_el or not link_el:
        return None

    title = (title_el.get_attribute("title") or title_el.inner_text()).strip()
    job_id = (link_el.get_attribute("data-jk") or "").strip()
    # Card hrefs are tracking redirects — build the canonical job URL instead.
    job_url = (f"{config.INDEED_BASE_URL}/viewjob?jk={job_id}" if job_id
               else config.INDEED_BASE_URL + (link_el.get_attribute("href") or ""))

    company_el = card.query_selector(_SELECTORS["job_company"])
    location_el = card.query_selector(_SELECTORS["job_location"])
    teaser_el = card.query_selector(_SELECTORS["job_teaser"])
    salary_el = card.query_selector(_SELECTORS["job_salary"])
    company = company_el.inner_text().strip() if company_el else ""

    return JobListing(
        job_key=make_job_key(SOURCE, job_id, title, company),
        title=title,
        company=company,
        location=location_el.inner_text().strip() if location_el else "",
        teaser=teaser_el.inner_text().strip() if teaser_el else "",
        url=job_url,
        source=SOURCE,
        salary=salary_el.inner_text().strip() if salary_el else "",
        search_keyword=search_keyword,
    )


def _scrape_search_page(page, keyword: str, page_num: int, debug: bool,
                        location: str = "") -> list[JobListing]:
    """Loads one search-result page (with retries) and extracts its listings."""
    url = _build_search_url(keyword, page_num, location)
    logging.info("[indeed] Fetching search page %d: %s", page_num, url)

    utils.retry(
        lambda: page.goto(url, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT_MS),
        retries=config.RETRY_ATTEMPTS,
        delay=config.RETRY_DELAY_SECONDS,
        backoff=config.RETRY_BACKOFF,
    )
    page.wait_for_timeout(config.RENDER_WAIT_MS)

    if _is_blocked(page):
        html_path = save_debug_html(page, f"indeed_blocked_page{page_num}")
        logging.warning(
            "[indeed] Blocked by anti-bot protection on %s — this happens "
            "intermittently with Indeed. Evidence saved to %s. Skipping "
            "the rest of this Indeed search.", url, html_path)
        return []

    if debug:
        save_debug_html(page, f"indeed_page{page_num}")

    cards = page.query_selector_all(_SELECTORS["job_card"])
    listings = []
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing:
            listings.append(listing)

    if not listings:
        html_path = save_debug_html(page, f"indeed_no_results_page{page_num}")
        logging.warning(
            "[indeed] 0 listings extracted from %s — either blocked or the "
            "site changed markup. Inspect %s and update SELECTORS in "
            "config.py.", url, html_path)

    return listings


# ======================================================
# JOB DETAIL PAGES
# ======================================================
def _fetch_job_details(context, url: str) -> tuple[str, str]:
    """
    Opens a job's detail page in a fresh tab and returns
    (full_description, salary). Salary is "" when the ad doesn't state one.
    Raises BlockedError when the page is an anti-bot challenge.
    """
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=config.PAGE_LOAD_TIMEOUT_MS)
        if _is_blocked(page):
            raise BlockedError(f"anti-bot verification page at {url}")
        try:
            page.wait_for_selector(_SELECTORS["job_detail_description"],
                                   timeout=config.DETAIL_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # The challenge can render after domcontentloaded — recheck
            # before treating this as an ordinary missing-selector timeout.
            if _is_blocked(page):
                raise BlockedError(f"anti-bot verification page at {url}")
            raise
        detail_el = page.query_selector(_SELECTORS["job_detail_description"])
        salary_el = page.query_selector(_SELECTORS["job_detail_salary"])
        description = detail_el.inner_text().strip() if detail_el else ""
        salary = salary_el.inner_text().strip() if salary_el else ""
        return description, salary
    finally:
        page.close()


def _fetch_full_descriptions(context, listings: list[JobListing],
                             delay_seconds: float) -> None:
    """Visits each job's detail page (rate limited) and fills in description."""
    logging.info("[indeed] Fetching full descriptions for %d jobs "
                 "(one request per %.1fs)...", len(listings), delay_seconds)
    fetched = 0
    for index, listing in enumerate(listings, start=1):
        try:
            description, salary = utils.retry(
                lambda: _fetch_job_details(context, listing.url),
                retries=config.RETRY_ATTEMPTS,
                delay=config.RETRY_DELAY_SECONDS,
                backoff=config.RETRY_BACKOFF,
                give_up_on=(BlockedError,),
            )
            listing.description = description
            if salary and not listing.salary:
                listing.salary = salary
            fetched += 1
        except BlockedError:
            # Retrying or continuing won't help — every remaining request
            # would hit the same wall. Card teasers still get scored.
            logging.warning(
                "[indeed] Anti-bot verification hit after %d/%d detail "
                "pages — skipping the rest; search-card teasers will be "
                "used for scoring instead. This is Indeed's Cloudflare "
                "protection and usually passes after a few hours.",
                fetched, len(listings))
            break
        except Exception as e:
            logging.error("[indeed] Could not fetch description for '%s' (%s): %s",
                          listing.title, listing.url, e)
        if index < len(listings):
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    logging.info("[indeed] Full descriptions fetched: %d/%d",
                 fetched, len(listings))


def _scrape_keyword(page, keyword: str, max_pages: int, delay_seconds: float,
                    debug: bool, location: str,
                    unique_listings: dict[str, JobListing]) -> int:
    """
    Scrapes all result pages for one keyword into unique_listings.
    Returns the number of duplicates skipped.
    """
    duplicates = 0
    for page_num in range(1, max_pages + 1):
        try:
            listings = _scrape_search_page(page, keyword, page_num, debug, location)
        except Exception as e:
            logging.error("[indeed] Failed to scrape search page %d: %s",
                          page_num, e)
            save_error_screenshot(page, f"indeed_search_page{page_num}")
            break

        if not listings:
            logging.warning("[indeed] No listings on page %d, stopping pagination.",
                            page_num)
            break

        for listing in listings:
            if listing.job_key in unique_listings:
                duplicates += 1
            else:
                unique_listings[listing.job_key] = listing
        logging.info("[indeed] Page %d: %d listings (%d unique so far)",
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
    Scrapes Indeed PH search results for one or more keywords (all in a
    single browser session), dedupes listings by job_key, and optionally
    visits each job's detail page for the full description.
    Owns the full browser lifecycle. Returns [] when blocked by anti-bot.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [keyword.strip() for keyword in keywords if keyword.strip()]

    unique_listings: dict[str, JobListing] = {}
    duplicates = 0
    browser = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=config.HEADLESS and not debug)
            context = browser.new_context(user_agent=config.USER_AGENT)
            page = context.new_page()

            for index, keyword in enumerate(keywords):
                logging.info("[indeed] Searching keyword %d/%d: '%s'%s",
                             index + 1, len(keywords), keyword,
                             f" in {location}" if location else "")
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug, location,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)

            if duplicates:
                logging.info("[indeed] Skipped %d duplicate listings "
                             "across pages/keywords.", duplicates)

            if fetch_details and unique_listings:
                _fetch_full_descriptions(context, list(unique_listings.values()),
                                         delay_seconds)
        finally:
            if browser:
                browser.close()
                logging.info("[indeed] Browser closed cleanly.")

    return list(unique_listings.values())

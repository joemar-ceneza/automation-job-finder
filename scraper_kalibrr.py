"""
scraper_kalibrr.py
Scrapes job listings from Kalibrr Philippines (kalibrr.com) for given
search terms using Playwright.

IMPORTANT (please read):
- Kalibrr's HTML/selectors change periodically. If a page yields zero
  results, its HTML is saved automatically to logs/debug_*.html -- open it,
  inspect the job card elements, and update SELECTORS["kalibrr"] in
  config.py.
- This scrapes publicly visible search-result pages only (no login, no
  personal data). Keep request volume low and keep the delays to avoid
  getting rate-limited or blocked. Personal/non-commercial use only.
- Kalibrr uses "Load More" button instead of pagination pages.
"""
import argparse
import logging
import re
import time
from dataclasses import asdict

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (AdGoneError, JobListing, make_job_key,
                            parse_relative_date, save_debug_html)

SOURCE = "kalibrr"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"/jobs/(\d+)/")


def _is_gone(page, response) -> bool:
    """True when the job ad was removed/expired (404 or takedown page)."""
    if response is not None and response.status in (404, 410):
        return True
    if "page not found" in (page.title() or "").lower():
        return True
    return "no longer available" in (page.content() or "").lower()


# ======================================================
# URL HELPERS
# ======================================================
def _build_search_url() -> str:
    """
    Builds the Kalibrr job board URL.
    Note: Kalibrr doesn't use URL parameters for search - we use the search input instead.
    """
    return f"{config.KALIBRR_BASE_URL}/home/all-jobs"


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    # Kalibrr cards have text in this format:
    # Line 0: Job title
    # Line 1: Company name
    card_text = card.inner_text().strip()
    lines = [line.strip() for line in card_text.split("\n") if line.strip()]

    if len(lines) < 2:
        return None  # Not a valid job card

    title = lines[0]
    company = lines[1]

    # Find the job link
    link_el = card.query_selector(_SELECTORS["job_link"])
    if not link_el:
        return None

    href = link_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.KALIBRR_BASE_URL + href
    # Clean up tracking parameters
    job_url = job_url.split("?")[0]

    # Optional fields
    location_el = card.query_selector(_SELECTORS["job_location"])
    teaser_el = card.query_selector(_SELECTORS["job_teaser"])
    salary_el = card.query_selector(_SELECTORS["job_salary"])
    date_el = card.query_selector(_SELECTORS["job_listing_date"])

    id_match = _JOB_ID_PATTERN.search(job_url)
    return JobListing(
        job_key=make_job_key(SOURCE, id_match.group(1) if id_match else "",
                             title, company),
        title=title,
        company=company,
        location=location_el.inner_text().strip() if location_el else "",
        teaser=teaser_el.inner_text().strip() if teaser_el else "",
        url=job_url,
        source=SOURCE,
        salary=salary_el.inner_text().strip() if salary_el else "",
        listing_date=parse_relative_date(date_el.inner_text()) if date_el else "",
        search_keyword=search_keyword,
    )


def _scrape_with_keyword(page, keyword: str, debug: bool,
                         max_loads: int = 5) -> list[JobListing]:
    """
    Searches for a keyword using the search input and loads more jobs.
    Returns all unique listings found.
    """
    url = _build_search_url()
    logging.info("[kalibrr] Loading job board: %s", url)

    utils.retry(
        lambda: page.goto(url, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT_MS),
        retries=config.RETRY_ATTEMPTS,
        delay=config.RETRY_DELAY_SECONDS,
        backoff=config.RETRY_BACKOFF,
    )
    page.wait_for_timeout(config.KALIBRR_RENDER_WAIT_MS)

    # Use the search input to search for keyword
    if keyword:
        logging.info("[kalibrr] Searching for: '%s'", keyword)
        search_input = page.query_selector('input[placeholder="Job Position"]')
        if search_input:
            search_input.fill(keyword)
            page.keyboard.press("Enter")
            page.wait_for_timeout(config.KALIBRR_RENDER_WAIT_MS)
        else:
            logging.warning("[kalibrr] Search input not found")

    if debug:
        save_debug_html(page, f"kalibrr_search_{keyword}")

    # Get initial listings
    unique_listings: dict[str, JobListing] = {}
    cards = page.query_selector_all(_SELECTORS["job_card"])
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing and listing.job_key not in unique_listings:
            unique_listings[listing.job_key] = listing

    logging.info("[kalibrr] Initial load: %d jobs", len(unique_listings))

    # Click "Load More" button multiple times
    for load_num in range(1, max_loads + 1):
        try:
            load_more = page.query_selector('button:has-text("Load More")')
            if not load_more:
                logging.info("[kalibrr] No more 'Load More' button found after %d loads",
                             load_num - 1)
                break

            logging.info("[kalibrr] Clicking 'Load More' (%d/%d)", load_num, max_loads)
            load_more.click()
            page.wait_for_timeout(config.KALIBRR_RENDER_WAIT_MS)

            # Extract new listings
            cards = page.query_selector_all(_SELECTORS["job_card"])
            new_count = 0
            for card in cards:
                listing = _extract_listing(card, keyword)
                if listing and listing.job_key not in unique_listings:
                    unique_listings[listing.job_key] = listing
                    new_count += 1

            logging.info("[kalibrr] Load %d: +%d new jobs (total: %d)",
                         load_num, new_count, len(unique_listings))

            if new_count == 0:
                logging.info("[kalibrr] No new jobs found, stopping load more")
                break

        except Exception as e:
            logging.error("[kalibrr] Failed to load more: %s", e)
            break

    return list(unique_listings.values())


# ======================================================
# JOB DETAIL PAGES
# ======================================================
def _fetch_job_details(context, url: str) -> tuple[str, str]:
    """
    Opens a job's detail page in a fresh tab and returns
    (full_description, salary). Salary is "" when the ad doesn't state one.
    Raises AdGoneError when the ad was removed after appearing in search.
    """
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded",
                             timeout=config.PAGE_LOAD_TIMEOUT_MS)
        if _is_gone(page, response):
            raise AdGoneError(f"job ad removed: {url}")
        try:
            page.wait_for_selector(_SELECTORS["job_detail_description"],
                                   timeout=config.DETAIL_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # Takedown pages can render after domcontentloaded -- recheck
            if _is_gone(page, response):
                raise AdGoneError(f"job ad removed: {url}")
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
    logging.info("[kalibrr] Fetching full descriptions for %d jobs "
                 "(one request per %.1fs)...", len(listings), delay_seconds)
    fetched = 0
    gone = 0
    for index, listing in enumerate(listings, start=1):
        try:
            description, salary = utils.retry(
                lambda: _fetch_job_details(context, listing.url),
                retries=config.RETRY_ATTEMPTS,
                delay=config.RETRY_DELAY_SECONDS,
                backoff=config.RETRY_BACKOFF,
                give_up_on=(AdGoneError,),
            )
            listing.description = description
            if salary and not listing.salary:
                listing.salary = salary
            fetched += 1
        except AdGoneError:
            gone += 1
            logging.info("[kalibrr] '%s' is no longer advertised -- "
                         "keeping the search-card teaser.", listing.title)
        except Exception as e:
            logging.error("[kalibrr] Could not fetch description for '%s' (%s): %s",
                          listing.title, listing.url, e)
        if index < len(listings):
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    logging.info("[kalibrr] Full descriptions fetched: %d/%d (%d ads "
                 "no longer advertised)", fetched, len(listings), gone)


# ======================================================
# PUBLIC ENTRY POINT
# ======================================================
def run_scraper(keywords: list[str] | str, max_pages: int = config.DEFAULT_PAGES,
                delay_seconds: float = config.DEFAULT_DELAY_SECONDS,
                debug: bool = False, fetch_details: bool = False) -> list[JobListing]:
    """
    Scrapes Kalibrr job search results for one or more keywords (all in a
    single browser session), dedupes listings by job_key across keywords,
    and optionally visits each job's detail page for the full description.
    Owns the full browser lifecycle.

    Note: Kalibrr uses "Load More" instead of pagination. max_pages is
    repurposed as the number of "Load More" clicks (default: 5).
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [keyword.strip() for keyword in keywords if keyword.strip()]

    unique_listings: dict[str, JobListing] = {}
    browser = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=config.HEADLESS and not debug)
            context = browser.new_context(user_agent=config.USER_AGENT)
            page = context.new_page()

            for index, keyword in enumerate(keywords):
                logging.info("[kalibrr] Searching keyword %d/%d: '%s'",
                             index + 1, len(keywords), keyword)
                listings = _scrape_with_keyword(page, keyword, debug,
                                                max_loads=max_pages)

                for listing in listings:
                    if listing.job_key not in unique_listings:
                        unique_listings[listing.job_key] = listing

                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            logging.info("[kalibrr] Total unique jobs: %d", len(unique_listings))

            if fetch_details and unique_listings:
                _fetch_full_descriptions(context, list(unique_listings.values()),
                                         delay_seconds)
        finally:
            if browser:
                browser.close()
                logging.info("[kalibrr] Browser closed cleanly.")

    return list(unique_listings.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Scrape Kalibrr job listings")
    parser.add_argument("keyword", help="Job title/keyword(s) to search, "
                        "comma-separated, e.g. 'python developer, automation engineer'")
    parser.add_argument("--pages", type=int, default=5,
                        help="Number of 'Load More' clicks (default: 5)")
    parser.add_argument("--delay", type=float, default=config.DEFAULT_DELAY_SECONDS,
                        help="Delay in seconds between keyword searches")
    parser.add_argument("--full-desc", action="store_true",
                        help="Fetch full job descriptions from detail pages (slower)")
    parser.add_argument("--debug", action="store_true",
                        help="Run browser visibly and save page HTML")

    args = parser.parse_args()
    keywords_list = [k.strip() for k in args.keyword.split(",")]
    results = run_scraper(keywords_list, args.pages, args.delay, args.debug,
                          args.full_desc)

    print(f"\n{'='*60}")
    print(f"Scraped {len(results)} unique listings from Kalibrr")
    print(f"{'='*60}\n")
    for job in results[:5]:  # show first 5
        print(f"* {job.title} at {job.company}")
        print(f"  {job.location} | {job.salary or 'No salary listed'}")
        print(f"  {job.url}\n")

"""
scraper_kalibrr.py
Scrapes job listings from Kalibrr Philippines (kalibrr.com) for given
search terms using Playwright.

IMPORTANT (please read):
- Kalibrr's HTML/selectors change periodically. If a page yields zero
  results, its HTML is saved automatically to logs/debug_*.html — open it,
  inspect the job card elements, and update SELECTORS["kalibrr"] in
  config.py.
- This scrapes publicly visible search-result pages only (no login, no
  personal data). Keep request volume low and keep the delays to avoid
  getting rate-limited or blocked. Personal/non-commercial use only.
"""
import argparse
import logging
import re
import time
import urllib.parse
from dataclasses import asdict

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (AdGoneError, JobListing, make_job_key,
                            parse_relative_date, save_debug_html,
                            save_error_screenshot)

SOURCE = "kalibrr"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"/(\d+)/")


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
def _build_search_url(keyword: str, page_num: int, location: str = "") -> str:
    """
    Builds the Kalibrr search URL for a keyword, page number, and
    optional location filter (e.g. "Metro Manila" or "Philippines").

    Kalibrr uses page= for pagination (1, 2, 3, etc.)
    """
    params = {
        "q": keyword.strip(),
        "page": str(page_num),
    }

    if location.strip():
        params["l"] = location.strip()

    query_string = urllib.parse.urlencode(params)
    return f"{config.KALIBRR_BASE_URL}/en-ph/job-board/te/technology?{query_string}"


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    title_el = card.query_selector(_SELECTORS["job_title"])
    if not title_el:
        return None  # not a job card

    title = title_el.inner_text().strip()

    # Kalibrr job cards have the link
    link_el = card.query_selector(_SELECTORS["job_link"])
    if not link_el:
        return None

    href = link_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.KALIBRR_BASE_URL + href
    # Clean up tracking parameters
    job_url = job_url.split("?")[0]

    company_el = card.query_selector(_SELECTORS["job_company"])
    location_el = card.query_selector(_SELECTORS["job_location"])
    teaser_el = card.query_selector(_SELECTORS["job_teaser"])
    salary_el = card.query_selector(_SELECTORS["job_salary"])
    date_el = card.query_selector(_SELECTORS["job_listing_date"])

    company = company_el.inner_text().strip() if company_el else ""

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


def _scrape_search_page(page, keyword: str, page_num: int, debug: bool,
                        location: str = "") -> list[JobListing]:
    """Loads one search-result page (with retries) and extracts its listings."""
    url = _build_search_url(keyword, page_num, location)
    logging.info("[kalibrr] Fetching search page %d: %s", page_num, url)

    utils.retry(
        lambda: page.goto(url, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT_MS),
        retries=config.RETRY_ATTEMPTS,
        delay=config.RETRY_DELAY_SECONDS,
        backoff=config.RETRY_BACKOFF,
    )
    # Kalibrr needs extra time for JS rendering
    page.wait_for_timeout(config.KALIBRR_RENDER_WAIT_MS)

    if debug:
        save_debug_html(page, f"kalibrr_page{page_num}")

    cards = page.query_selector_all(_SELECTORS["job_card"])
    listings = []
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing:
            listings.append(listing)

    if not listings:
        # Selectors may have changed — always keep evidence for troubleshooting.
        html_path = save_debug_html(page, f"kalibrr_no_results_page{page_num}")
        logging.warning(
            "[kalibrr] 0 listings extracted from %s — the site may have "
            "changed markup. Inspect %s and update SELECTORS in config.py.",
            url, html_path)

    return listings


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
            # Takedown pages can render after domcontentloaded — recheck
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
            logging.info("[kalibrr] '%s' is no longer advertised — "
                         "keeping the search-card teaser.", listing.title)
        except Exception as e:
            logging.error("[kalibrr] Could not fetch description for '%s' (%s): %s",
                          listing.title, listing.url, e)
        if index < len(listings):
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    logging.info("[kalibrr] Full descriptions fetched: %d/%d (%d ads "
                 "no longer advertised)", fetched, len(listings), gone)


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
            logging.error("[kalibrr] Failed to scrape search page %d: %s",
                          page_num, e)
            save_error_screenshot(page, f"kalibrr_search_page{page_num}")
            break

        if not listings:
            logging.warning("[kalibrr] No listings on page %d, stopping pagination.",
                            page_num)
            break

        for listing in listings:
            if listing.job_key in unique_listings:
                duplicates += 1
            else:
                unique_listings[listing.job_key] = listing
        logging.info("[kalibrr] Page %d: %d listings (%d unique so far)",
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
    Scrapes Kalibrr job search results for one or more keywords (all in a
    single browser session), dedupes listings by job_key across keywords,
    and optionally visits each job's detail page for the full description.
    Owns the full browser lifecycle.
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
                logging.info("[kalibrr] Searching keyword %d/%d: '%s'%s",
                             index + 1, len(keywords), keyword,
                             f" in {location}" if location else " in Philippines")
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug, location,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            if duplicates:
                logging.info("[kalibrr] Skipped %d duplicate listings "
                             "across pages/keywords.", duplicates)

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
    parser.add_argument("--pages", type=int, default=config.DEFAULT_PAGES,
                        help="Number of search pages to scrape per keyword")
    parser.add_argument("--delay", type=float, default=config.DEFAULT_DELAY_SECONDS,
                        help="Delay in seconds between page requests")
    parser.add_argument("--location", default="",
                        help="Location filter, e.g. 'Metro Manila'")
    parser.add_argument("--full-desc", action="store_true",
                        help="Fetch full job descriptions from detail pages (slower)")
    parser.add_argument("--debug", action="store_true",
                        help="Run browser visibly and save page HTML")

    args = parser.parse_args()
    keywords_list = [k.strip() for k in args.keyword.split(",")]
    results = run_scraper(keywords_list, args.pages, args.delay, args.debug,
                          args.full_desc, args.location)

    print(f"\n{'='*60}")
    print(f"Scraped {len(results)} unique listings from Kalibrr")
    print(f"{'='*60}\n")
    for job in results[:5]:  # show first 5
        print(f"• {job.title} at {job.company}")
        print(f"  {job.location} | {job.salary or 'No salary listed'}")
        print(f"  {job.url}\n")

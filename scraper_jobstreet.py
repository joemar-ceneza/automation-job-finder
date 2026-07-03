"""
scraper_jobstreet.py
Scrapes job listings from JobStreet Philippines (ph.jobstreet.com) for given
search terms using Playwright.

IMPORTANT (please read):
- JobStreet's HTML/selectors change periodically. If a page yields zero
  results, its HTML is saved automatically to logs/debug_*.html — open it,
  inspect the job card elements, and update SELECTORS["jobstreet"] in
  config.py.
- This scrapes publicly visible search-result pages only (no login, no
  personal data). Keep request volume low and keep the delays to avoid
  getting rate-limited or blocked. Personal/non-commercial use only.
"""
import argparse
import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import asdict

from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (JobListing, make_job_key, parse_relative_date,
                            save_debug_html, save_error_screenshot)

SOURCE = "jobstreet"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"/job/(\d+)")


# ======================================================
# URL HELPERS
# ======================================================
def _build_search_url(keyword: str, page_num: int, location: str = "") -> str:
    """
    Builds the JobStreet PH search URL for a keyword, page number, and
    optional location filter (e.g. "Metro Manila" -> /in-Metro-Manila).
    """
    slug = urllib.parse.quote(keyword.strip().lower().replace(" ", "-"))
    url = f"{config.JOBSTREET_BASE_URL}/{slug}-jobs"
    if location.strip():
        location_slug = urllib.parse.quote(location.strip().replace(" ", "-"))
        url += f"/in-{location_slug}"
    if page_num > 1:
        url += f"?page={page_num}"
    return url


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    title_el = card.query_selector(_SELECTORS["job_title"])
    if not title_el:
        return None  # not a job card (nav/footer/etc.)

    title = title_el.inner_text().strip()
    href = title_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.JOBSTREET_BASE_URL + href

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
    logging.info("[jobstreet] Fetching search page %d: %s", page_num, url)

    utils.retry(
        lambda: page.goto(url, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT_MS),
        retries=config.RETRY_ATTEMPTS,
        delay=config.RETRY_DELAY_SECONDS,
        backoff=config.RETRY_BACKOFF,
    )
    # Give the page a moment for JS-rendered content to settle.
    page.wait_for_timeout(config.RENDER_WAIT_MS)

    if debug:
        save_debug_html(page, f"jobstreet_page{page_num}")

    cards = page.query_selector_all(_SELECTORS["job_card"])
    listings = []
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing:
            listings.append(listing)

    if not listings:
        # Selectors may have changed — always keep evidence for troubleshooting.
        html_path = save_debug_html(page, f"jobstreet_no_results_page{page_num}")
        logging.warning(
            "[jobstreet] 0 listings extracted from %s — the site may have "
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
    """
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded",
                  timeout=config.PAGE_LOAD_TIMEOUT_MS)
        page.wait_for_selector(_SELECTORS["job_detail_description"],
                               timeout=config.DETAIL_WAIT_TIMEOUT_MS)
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
    logging.info("[jobstreet] Fetching full descriptions for %d jobs "
                 "(one request per %.1fs)...", len(listings), delay_seconds)
    fetched = 0
    for index, listing in enumerate(listings, start=1):
        try:
            description, salary = utils.retry(
                lambda: _fetch_job_details(context, listing.url),
                retries=config.RETRY_ATTEMPTS,
                delay=config.RETRY_DELAY_SECONDS,
                backoff=config.RETRY_BACKOFF,
            )
            listing.description = description
            if salary and not listing.salary:
                listing.salary = salary
            fetched += 1
        except Exception as e:
            logging.error("[jobstreet] Could not fetch description for '%s' (%s): %s",
                          listing.title, listing.url, e)
        if index < len(listings):
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    logging.info("[jobstreet] Full descriptions fetched: %d/%d",
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
            logging.error("[jobstreet] Failed to scrape search page %d: %s",
                          page_num, e)
            save_error_screenshot(page, f"jobstreet_search_page{page_num}")
            break

        if not listings:
            logging.warning("[jobstreet] No listings on page %d, stopping pagination.",
                            page_num)
            break

        for listing in listings:
            if listing.job_key in unique_listings:
                duplicates += 1
            else:
                unique_listings[listing.job_key] = listing
        logging.info("[jobstreet] Page %d: %d listings (%d unique so far)",
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
    Scrapes JobStreet PH search results for one or more keywords (all in a
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
                logging.info("[jobstreet] Searching keyword %d/%d: '%s'%s",
                             index + 1, len(keywords), keyword,
                             f" in {location}" if location else "")
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug, location,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            if duplicates:
                logging.info("[jobstreet] Skipped %d duplicate listings "
                             "across pages/keywords.", duplicates)

            if fetch_details and unique_listings:
                _fetch_full_descriptions(context, list(unique_listings.values()),
                                         delay_seconds)
        finally:
            if browser:
                browser.close()
                logging.info("[jobstreet] Browser closed cleanly.")

    return list(unique_listings.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Scrape JobStreet PH job listings")
    parser.add_argument("keyword", help="Job title/keyword(s) to search, "
                        "comma-separated, e.g. 'python developer, automation engineer'")
    parser.add_argument("--pages", type=int, default=config.DEFAULT_PAGES,
                        help="Number of search-result pages to scrape")
    parser.add_argument("--delay", type=float, default=config.DEFAULT_DELAY_SECONDS,
                        help="Seconds to wait between page requests")
    parser.add_argument("--location", default="",
                        help="Limit results to a location, e.g. 'Metro Manila'")
    parser.add_argument("--debug", action="store_true",
                        help="Run visibly and save page HTML for every page")
    parser.add_argument("--full-desc", action="store_true",
                        help="Also visit each job's detail page for the full description")
    parser.add_argument("--out", default=os.path.join(config.OUTPUT_DIR, "jobs_raw.json"),
                        help="Output JSON file")
    args = parser.parse_args()

    results = run_scraper(args.keyword.split(","), max_pages=args.pages,
                          delay_seconds=args.delay, debug=args.debug,
                          fetch_details=args.full_desc, location=args.location)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)

    logging.info("Total unique listings scraped: %d", len(results))
    logging.info("Saved to %s", args.out)

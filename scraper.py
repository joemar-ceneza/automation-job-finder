"""
scraper.py
Scrapes job listings from JobStreet Philippines (ph.jobstreet.com) for a given
search term using Playwright.

IMPORTANT (please read):
- JobStreet's HTML/selectors change periodically. If a page yields zero
  results, its HTML is saved automatically to logs/debug_*.html — open it,
  inspect the job card elements, and update SELECTORS in config.py.
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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

import config
import utils


@dataclass
class JobListing:
    job_key: str
    title: str
    company: str
    location: str
    teaser: str
    url: str
    salary: str = ""
    description: str = ""
    listing_date: str = ""     # ISO date derived from "3d ago" at scrape time
    search_keyword: str = ""   # which search term found this listing


# ======================================================
# URL / DEDUPE KEY HELPERS
# ======================================================
_JOB_ID_PATTERN = re.compile(r"/job/(\d+)")


def _build_search_url(keyword: str, page_num: int, location: str = "") -> str:
    """
    Builds the JobStreet PH search URL for a keyword, page number, and
    optional location filter (e.g. "Metro Manila" -> /in-Metro-Manila).
    """
    slug = urllib.parse.quote(keyword.strip().lower().replace(" ", "-"))
    url = f"{config.BASE_URL}/{slug}-jobs"
    if location.strip():
        location_slug = urllib.parse.quote(location.strip().replace(" ", "-"))
        url += f"/in-{location_slug}"
    if page_num > 1:
        url += f"?page={page_num}"
    return url


_RELATIVE_DATE_PATTERN = re.compile(r"(\d+)\s*(m|h|d)\b", re.IGNORECASE)


def _parse_listing_date(raw_text: str) -> str:
    """
    Converts JobStreet's relative age ("11h ago", "3d ago", "30d+ ago")
    to an absolute ISO date so it stays meaningful in the database.
    Returns "" when the text doesn't match.
    """
    match = _RELATIVE_DATE_PATTERN.search(raw_text)
    if not match:
        return ""
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        delta = timedelta(minutes=amount)
    elif unit == "h":
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(days=amount)
    return (datetime.now() - delta).date().isoformat()


def _make_job_key(title: str, company: str, url: str) -> str:
    """
    Stable dedupe key for a listing: JobStreet's numeric job id when present
    in the URL, otherwise normalized title+company.
    """
    id_match = _JOB_ID_PATTERN.search(url)
    if id_match:
        return f"id:{id_match.group(1)}"
    title_norm = re.sub(r"\s+", " ", title.lower()).strip()
    company_norm = re.sub(r"\s+", " ", company.lower()).strip()
    return f"tc:{title_norm}|{company_norm}"


# ======================================================
# DEBUG SNAPSHOTS
# ======================================================
def _save_debug_html(page, label: str) -> str:
    """Saves the current page HTML to logs/ for selector troubleshooting."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(config.LOGS_DIR, f"debug_{label}_{timestamp}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    logging.info("Saved page HTML to %s", path)
    return path


def _save_error_screenshot(page, label: str) -> None:
    """Saves a screenshot to logs/screenshots/ after a scraping failure."""
    try:
        os.makedirs(config.SCREENSHOTS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(config.SCREENSHOTS_DIR, f"error_{label}_{timestamp}.png")
        page.screenshot(path=path)
        logging.info("Saved error screenshot to %s", path)
    except Exception as e:
        logging.warning("Could not save error screenshot: %s", e)


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    title_el = card.query_selector(config.SELECTORS["job_title"])
    if not title_el:
        return None  # not a job card (nav/footer/etc.)

    title = title_el.inner_text().strip()
    href = title_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.BASE_URL + href

    company_el = card.query_selector(config.SELECTORS["job_company"])
    location_el = card.query_selector(config.SELECTORS["job_location"])
    teaser_el = card.query_selector(config.SELECTORS["job_teaser"])
    salary_el = card.query_selector(config.SELECTORS["job_salary"])
    date_el = card.query_selector(config.SELECTORS["job_listing_date"])
    company = company_el.inner_text().strip() if company_el else ""

    return JobListing(
        job_key=_make_job_key(title, company, job_url),
        title=title,
        company=company,
        location=location_el.inner_text().strip() if location_el else "",
        teaser=teaser_el.inner_text().strip() if teaser_el else "",
        url=job_url,
        salary=salary_el.inner_text().strip() if salary_el else "",
        listing_date=_parse_listing_date(date_el.inner_text()) if date_el else "",
        search_keyword=search_keyword,
    )


def _scrape_search_page(page, keyword: str, page_num: int, debug: bool,
                        location: str = "") -> list[JobListing]:
    """Loads one search-result page (with retries) and extracts its listings."""
    url = _build_search_url(keyword, page_num, location)
    logging.info("Fetching search page %d: %s", page_num, url)

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
        _save_debug_html(page, f"page{page_num}")

    cards = page.query_selector_all(config.SELECTORS["job_card"])
    listings = []
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing:
            listings.append(listing)

    if not listings:
        # Selectors may have changed — always keep evidence for troubleshooting.
        html_path = _save_debug_html(page, f"no_results_page{page_num}")
        logging.warning(
            "0 listings extracted from %s — JobStreet may have changed markup. "
            "Inspect %s and update SELECTORS in config.py.", url, html_path)

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
        page.wait_for_selector(config.SELECTORS["job_detail_description"],
                               timeout=config.DETAIL_WAIT_TIMEOUT_MS)
        detail_el = page.query_selector(config.SELECTORS["job_detail_description"])
        salary_el = page.query_selector(config.SELECTORS["job_detail_salary"])
        description = detail_el.inner_text().strip() if detail_el else ""
        salary = salary_el.inner_text().strip() if salary_el else ""
        return description, salary
    finally:
        page.close()


def _fetch_full_descriptions(context, listings: list[JobListing],
                             delay_seconds: float) -> None:
    """Visits each job's detail page (rate limited) and fills in description."""
    logging.info("Fetching full descriptions for %d jobs (one request per %.1fs)...",
                 len(listings), delay_seconds)
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
            logging.error("Could not fetch description for '%s' (%s): %s",
                          listing.title, listing.url, e)
        if index < len(listings):
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    logging.info("Full descriptions fetched: %d/%d", fetched, len(listings))


# ======================================================
# PUBLIC ENTRY POINT
# ======================================================
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
            logging.error("Failed to scrape search page %d: %s", page_num, e)
            _save_error_screenshot(page, f"search_page{page_num}")
            break

        if not listings:
            logging.warning("No listings on page %d, stopping pagination.", page_num)
            break

        for listing in listings:
            if listing.job_key in unique_listings:
                duplicates += 1
            else:
                unique_listings[listing.job_key] = listing
        logging.info("Page %d: %d listings (%d unique so far)",
                     page_num, len(listings), len(unique_listings))

        if page_num < max_pages:
            time.sleep(delay_seconds)  # be polite, avoid rate limits
    return duplicates


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
                logging.info("Searching keyword %d/%d: '%s'%s",
                             index + 1, len(keywords), keyword,
                             f" in {location}" if location else "")
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug, location,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            if duplicates:
                logging.info("Skipped %d duplicate listings across pages/keywords.",
                             duplicates)

            if fetch_details and unique_listings:
                _fetch_full_descriptions(context, list(unique_listings.values()),
                                         delay_seconds)
        finally:
            if browser:
                browser.close()
                logging.info("Browser closed cleanly.")

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

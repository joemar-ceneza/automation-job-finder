"""
scraper_indeed.py
Scrapes job listings from Indeed Philippines (ph.indeed.com) for given
search terms using Playwright.

IMPORTANT (please read):
- Indeed's HTML/selectors change periodically. If a page yields zero
  results, its HTML is saved automatically to logs/debug_*.html — open it,
  inspect the job card elements, and update SELECTORS["indeed"] in
  config.py.
- This scrapes publicly visible search-result pages only (no login, no
  personal data). Keep request volume low and keep the delays to avoid
  getting rate-limited or blocked. Personal/non-commercial use only.
- Indeed requires clicking the "Next" button for pagination rather than
  direct URL navigation (anti-bot protection).
"""
import argparse
import logging
import re
import time
import urllib.parse

from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (AntiBotBlockedError, JobListing,
                            fetch_full_descriptions, is_blocked, make_job_key,
                            parse_relative_date, save_debug_html,
                            save_error_screenshot, text_from)

SOURCE = "indeed"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"jk=([a-zA-Z0-9]+)")


# ======================================================
# URL HELPERS
# ======================================================
def probe_url(keyword: str) -> str:
    """One representative search URL, for --check-selectors."""
    return _build_search_url(keyword, 1)


def _build_search_url(keyword: str, page_num: int, location: str = "") -> str:
    """
    Builds the Indeed Philippines search URL for a keyword, page number, and
    optional location filter (e.g. "Metro Manila" or "Philippines").

    Indeed uses start= for pagination (0, 10, 20, etc.)
    """
    # Indeed uses start offset rather than page numbers (10 results per page)
    start_offset = (page_num - 1) * 10

    params = {
        "q": keyword.strip(),
        "l": location.strip() if location.strip() else "Philippines",
        "start": str(start_offset),
        "fromage": "7",  # Jobs posted in last 7 days
    }

    query_string = urllib.parse.urlencode(params)
    return f"{config.INDEED_BASE_URL}/jobs?{query_string}"


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    title_el = card.query_selector(_SELECTORS["job_title"])
    if not title_el:
        return None  # not a job card

    title = title_el.inner_text().strip()

    # Indeed job cards have the link on the title
    link_el = card.query_selector(_SELECTORS["job_link"])
    if not link_el:
        return None

    href = link_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.INDEED_BASE_URL + href
    # Drop tracking parameters but keep jk= (the job key).
    if "?" in job_url:
        base, params_str = job_url.split("?", 1)
        jk_match = _JOB_ID_PATTERN.search(params_str)
        if jk_match:
            job_url = f"{base}?jk={jk_match.group(1)}"

    company = text_from(card, _SELECTORS.get("job_company"))

    id_match = _JOB_ID_PATTERN.search(job_url)
    return JobListing(
        job_key=make_job_key(SOURCE, id_match.group(1) if id_match else "",
                             title, company),
        title=title,
        company=company,
        location=text_from(card, _SELECTORS.get("job_location")),
        teaser=text_from(card, _SELECTORS.get("job_teaser")),
        url=job_url,
        source=SOURCE,
        salary=text_from(card, _SELECTORS.get("job_salary")),
        listing_date=parse_relative_date(
            text_from(card, _SELECTORS.get("job_listing_date"))),
        search_keyword=search_keyword,
    )


def _scrape_search_page(page, keyword: str, page_num: int, debug: bool,
                        location: str = "", first_page: bool = True) -> list[JobListing]:
    """
    Loads one search-result page and extracts its listings.
    For page 1, navigates to the URL directly.
    For subsequent pages, clicks the "Next" button (Indeed anti-bot protection).
    """
    if first_page:
        # First page: navigate to the URL
        url = _build_search_url(keyword, page_num, location)
        logging.info("[indeed] Fetching search page %d: %s", page_num, url)

        utils.retry(
            lambda: page.goto(url, wait_until="domcontentloaded",
                              timeout=config.PAGE_LOAD_TIMEOUT_MS),
            retries=config.RETRY_ATTEMPTS,
            delay=config.RETRY_DELAY_SECONDS,
            backoff=config.RETRY_BACKOFF,
        )
        # Give the page a moment for JS-rendered content to settle.
        page.wait_for_timeout(config.RENDER_WAIT_MS)
    else:
        logging.info("[indeed] Clicking 'Next' to go to page %d", page_num)
        try:
            # Re-query inside the retried callable. Holding an ElementHandle
            # across attempts is pointless: once a click starts navigating, the
            # handle detaches and every retry fails on a stale element rather
            # than on the thing that actually went wrong.
            utils.retry(
                lambda: page.click(_SELECTORS["next_button"],
                                   timeout=config.DETAIL_WAIT_TIMEOUT_MS),
                retries=config.RETRY_ATTEMPTS,
                delay=config.RETRY_DELAY_SECONDS,
                backoff=config.RETRY_BACKOFF,
            )
            page.wait_for_timeout(config.RENDER_WAIT_MS * 2)
        except Exception as error:
            if is_blocked(page):
                raise AntiBotBlockedError(
                    f"blocked before page {page_num}") from error
            logging.warning("[indeed] No usable 'Next' button on page %d (%s) "
                            "— treating that as the last page.",
                            page_num - 1, error)
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
        # A bot challenge and a markup change look identical from here — both
        # yield zero cards — but they need opposite responses, so tell them
        # apart before advising anyone to go and edit selectors.
        if is_blocked(page):
            save_debug_html(page, f"indeed_blocked_page{page_num}")
            raise AntiBotBlockedError(
                f"Indeed served a verification challenge on page {page_num}")
        html_path = save_debug_html(page, f"indeed_no_results_page{page_num}")
        logging.warning(
            "[indeed] 0 listings extracted from page %d -- the site may have "
            "changed markup. Inspect %s and update SELECTORS in config.py.",
            page_num, html_path)

    return listings


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
            listings = _scrape_search_page(
                page, keyword, page_num, debug, location,
                first_page=(page_num == 1))
        except AntiBotBlockedError as error:
            # Expected on page 2+: Indeed fronts pagination with Cloudflare.
            # Say so plainly instead of implying the selectors need editing,
            # and keep whatever earlier pages returned.
            logging.warning(
                "[indeed] %s. Pages beyond %d are not reachable without "
                "defeating that challenge, which this tool does not do — "
                "keeping the %d listing(s) already found. Use --pages 1 for "
                "Indeed to skip this.", error, page_num - 1,
                len(unique_listings))
            break
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
    Scrapes Indeed Philippines job search results for one or more keywords
    (all in a single browser session), dedupes listings by job_key across
    keywords, and optionally visits each job's detail page for the full
    description. Owns the full browser lifecycle.
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
                             f" in {location}" if location else " in Philippines")
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug, location,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            if duplicates:
                logging.info("[indeed] Skipped %d duplicate listings "
                             "across pages/keywords.", duplicates)

            if fetch_details and unique_listings:
                fetch_full_descriptions(SOURCE, _SELECTORS, context,
                                        list(unique_listings.values()),
                                        delay_seconds)
        finally:
            if browser:
                browser.close()
                logging.info("[indeed] Browser closed cleanly.")

    return list(unique_listings.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Scrape Indeed Philippines job listings")
    parser.add_argument("keyword", help="Job title/keyword(s) to search, "
                        "comma-separated, e.g. 'python developer, automation engineer'")
    parser.add_argument("--pages", type=int, default=config.DEFAULT_PAGES,
                        help="Number of search pages to scrape per keyword")
    parser.add_argument("--delay", type=float, default=config.delay_for(SOURCE),
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
    print(f"Scraped {len(results)} unique listings from Indeed Philippines")
    print(f"{'='*60}\n")
    for job in results[:5]:  # show first 5
        print(f"* {job.title} at {job.company}")
        print(f"  {job.location} | {job.salary or 'No salary listed'}")
        print(f"  {job.url}\n")

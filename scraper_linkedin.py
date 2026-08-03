"""
scraper_linkedin.py
Scrapes job listings from LinkedIn Philippines (linkedin.com/jobs) for given
search terms using Playwright.

IMPORTANT (please read):
- LinkedIn's HTML/selectors change periodically. If a page yields zero
  results, its HTML is saved automatically to logs/debug_*.html — open it,
  inspect the job card elements, and update SELECTORS["linkedin"] in
  config.py.
- This scrapes publicly visible search-result pages only (no login, no
  personal data). Keep request volume low and keep the delays to avoid
  getting rate-limited or blocked. Personal/non-commercial use only.
- LinkedIn is more aggressive with rate limiting than other job sites.
  The default delays are intentionally conservative.
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

SOURCE = "linkedin"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"/jobs/view/(\d+)")
# LinkedIn's takedown wording differs from the other sites'.
_GONE_MARKER = "no longer accepting applications"


# ======================================================
# URL HELPERS
# ======================================================
def probe_url(keyword: str) -> str:
    """One representative search URL, for --check-selectors."""
    return _build_search_url(keyword, 1)


def _build_search_url(keyword: str, page_num: int, location: str = "") -> str:
    """
    Builds the LinkedIn jobs search URL for a keyword, page number, and
    optional location filter (e.g. "Metro Manila" or "Philippines").

    LinkedIn uses start= for pagination (0, 25, 50, etc.)
    """
    # LinkedIn uses start offset rather than page numbers
    start_offset = (page_num - 1) * 25

    params = {
        "keywords": keyword.strip(),
        "location": location.strip() if location.strip() else "Philippines",
        "start": str(start_offset),
        "f_TPR": "r86400",  # Optional: jobs posted in last 24h, remove or adjust as needed
    }

    query_string = urllib.parse.urlencode(params)
    return f"{config.LINKEDIN_BASE_URL}/jobs/search?{query_string}"


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    title_el = card.query_selector(_SELECTORS["job_title"])
    if not title_el:
        return None  # not a job card

    title = title_el.inner_text().strip()

    # LinkedIn job cards have the link on the card itself
    link_el = card.query_selector(_SELECTORS["job_link"])
    if not link_el:
        return None

    href = link_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.LINKEDIN_BASE_URL + href
    # Clean up tracking parameters
    job_url = job_url.split("?")[0]

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
                        location: str = "") -> list[JobListing]:
    """Loads one search-result page (with retries) and extracts its listings."""
    url = _build_search_url(keyword, page_num, location)
    logging.info("[linkedin] Fetching search page %d: %s", page_num, url)

    utils.retry(
        lambda: page.goto(url, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT_MS),
        retries=config.RETRY_ATTEMPTS,
        delay=config.RETRY_DELAY_SECONDS,
        backoff=config.RETRY_BACKOFF,
    )
    # LinkedIn needs extra time for JS rendering
    page.wait_for_timeout(config.LINKEDIN_RENDER_WAIT_MS)

    if debug:
        save_debug_html(page, f"linkedin_page{page_num}")

    cards = page.query_selector_all(_SELECTORS["job_card"])
    listings = []
    for card in cards:
        listing = _extract_listing(card, keyword)
        if listing:
            listings.append(listing)

    if not listings:
        # A sign-in wall or bot challenge yields zero cards exactly like a
        # markup change does, so separate them before blaming the selectors.
        if is_blocked(page):
            save_debug_html(page, f"linkedin_blocked_page{page_num}")
            raise AntiBotBlockedError(
                f"LinkedIn served a verification or sign-in wall on page "
                f"{page_num}")
        html_path = save_debug_html(page, f"linkedin_no_results_page{page_num}")
        logging.warning(
            "[linkedin] 0 listings extracted from %s — the site may have "
            "changed markup. Inspect %s and update SELECTORS in config.py.",
            url, html_path)

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
            listings = _scrape_search_page(page, keyword, page_num, debug, location)
        except AntiBotBlockedError as error:
            logging.warning(
                "[linkedin] %s. Keeping the %d listing(s) already found; "
                "getting past this would mean defeating an access control, "
                "which this tool does not do.", error, len(unique_listings))
            break
        except Exception as e:
            logging.error("[linkedin] Failed to scrape search page %d: %s",
                          page_num, e)
            save_error_screenshot(page, f"linkedin_search_page{page_num}")
            break

        if not listings:
            logging.warning("[linkedin] No listings on page %d, stopping pagination.",
                            page_num)
            break

        for listing in listings:
            if listing.job_key in unique_listings:
                duplicates += 1
            else:
                unique_listings[listing.job_key] = listing
        logging.info("[linkedin] Page %d: %d listings (%d unique so far)",
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
    Scrapes LinkedIn jobs search results for one or more keywords (all in a
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
                logging.info("[linkedin] Searching keyword %d/%d: '%s'%s",
                             index + 1, len(keywords), keyword,
                             f" in {location}" if location else " in Philippines")
                duplicates += _scrape_keyword(page, keyword, max_pages,
                                              delay_seconds, debug, location,
                                              unique_listings)
                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            if duplicates:
                logging.info("[linkedin] Skipped %d duplicate listings "
                             "across pages/keywords.", duplicates)

            if fetch_details and unique_listings:
                fetch_full_descriptions(SOURCE, _SELECTORS, context,
                                        list(unique_listings.values()),
                                        delay_seconds, _GONE_MARKER)
        finally:
            if browser:
                browser.close()
                logging.info("[linkedin] Browser closed cleanly.")

    return list(unique_listings.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Scrape LinkedIn job listings")
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
    print(f"Scraped {len(results)} unique listings from LinkedIn")
    print(f"{'='*60}\n")
    for job in results[:5]:  # show first 5
        print(f"• {job.title} at {job.company}")
        print(f"  {job.location} | {job.salary or 'No salary listed'}")
        print(f"  {job.url}\n")

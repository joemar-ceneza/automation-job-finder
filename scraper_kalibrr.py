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

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
import utils
from scraper_common import (AntiBotBlockedError, JobListing,
                            fetch_full_descriptions, is_blocked, make_job_key,
                            parse_relative_date, save_debug_html,
                            save_error_screenshot, text_from)

SOURCE = "kalibrr"
_SELECTORS = config.SELECTORS[SOURCE]
_JOB_ID_PATTERN = re.compile(r"/jobs/(\d+)/")


class SearchUnavailableError(Exception):
    """
    Raised when Kalibrr's search box cannot be found.

    This has to be fatal rather than a warning. Kalibrr filters by typing into
    an input rather than by URL, so if the input is missing the page still shows
    the full unfiltered job board — and scraping that would tag hundreds of
    unrelated listings with the keyword you searched for. `search_keyword` is
    the column salary banding, skill demand and --learn all group by, so
    carrying on quietly poisons those for every future run.
    """


# ======================================================
# URL HELPERS
# ======================================================
def probe_url(keyword: str = "") -> str:
    """
    One representative URL, for --check-selectors. Kalibrr filters by typing
    into the page rather than by URL, so the keyword plays no part here.
    """
    return _build_search_url()


def _build_search_url() -> str:
    """
    Builds the Kalibrr job board URL.
    Note: Kalibrr doesn't use URL parameters for search - we use the search input instead.
    """
    return f"{config.KALIBRR_BASE_URL}/home/all-jobs"


# ======================================================
# SEARCH RESULT PAGES
# ======================================================
def _title_and_company(card) -> tuple[str, str]:
    """
    Title and company from a card, preferring selectors over line position.

    Reading lines[0] and lines[1] out of the card text works right up until a
    card carries a "Featured" or "Urgent Hiring" badge, which shifts every field
    by one and files the real title as the company. Selectors are tried first;
    the line split stays as a fallback for cards that lack them.
    """
    title = text_from(card, _SELECTORS.get("job_title"))
    company = text_from(card, _SELECTORS.get("job_company"))
    if title and company:
        return title, company

    lines = [line.strip() for line in card.inner_text().split("\n")
             if line.strip()]
    if len(lines) < 2:
        return title, company
    return title or lines[0], company or lines[1]


def _extract_listing(card, search_keyword: str) -> JobListing | None:
    """Extracts one JobListing from a search-result card element."""
    link_el = card.query_selector(_SELECTORS["job_link"])
    if not link_el:
        return None                        # not a job card

    title, company = _title_and_company(card)
    if not title:
        return None

    href = link_el.get_attribute("href") or ""
    job_url = href if href.startswith("http") else config.KALIBRR_BASE_URL + href
    job_url = job_url.split("?")[0]        # drop tracking parameters

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


def _apply_search(page, keyword: str) -> None:
    """
    Types the keyword into Kalibrr's search box. Raises when the box is missing,
    because the alternative is silently scraping the unfiltered board.
    """
    if not keyword:
        return
    logging.info("[kalibrr] Searching for: '%s'", keyword)
    search_input = page.query_selector(_SELECTORS["search_input"])
    if not search_input:
        html_path = save_debug_html(page, "kalibrr_no_search_input")
        raise SearchUnavailableError(
            f"Kalibrr's search input {_SELECTORS['search_input']!r} was not "
            f"found, so '{keyword}' could not be applied. Refusing to scrape "
            f"the unfiltered job board — every listing would be mislabelled as "
            f"a match for this keyword. Inspect {html_path} and update "
            f"SELECTORS['kalibrr']['search_input'] in config.py.")
    search_input.fill(keyword)
    page.keyboard.press("Enter")
    page.wait_for_timeout(config.KALIBRR_RENDER_WAIT_MS)


def _harvest(page, keyword: str,
             unique_listings: dict[str, JobListing], seen_cards: int) -> int:
    """
    Extracts cards added since the last pass into unique_listings.
    Returns the new total card count, so each card is only ever parsed once.
    """
    cards = page.query_selector_all(_SELECTORS["job_card"])
    for card in cards[seen_cards:]:
        listing = _extract_listing(card, keyword)
        if listing and listing.job_key not in unique_listings:
            unique_listings[listing.job_key] = listing
    return len(cards)


def _click_load_more(page, card_count: int) -> bool:
    """
    Clicks "Load More" and waits for the card count to actually grow.

    Waiting on the DOM rather than a fixed sleep is what makes this reliable:
    a fixed wait is a bet that rendering finishes in time, and losing that bet
    looks exactly like "no more results".
    Returns False when there is no button left to click.
    """
    button = page.query_selector(_SELECTORS["load_more"])
    if not button:
        return False
    button.click()
    page.wait_for_function(
        "([selector, previous]) => "
        "document.querySelectorAll(selector).length > previous",
        arg=[_SELECTORS["job_card"], card_count],
        timeout=config.LOAD_MORE_TIMEOUT_MS)
    return True


def _scrape_with_keyword(page, keyword: str, debug: bool,
                         max_loads: int) -> list[JobListing]:
    """Searches for a keyword, expands the results, and returns the listings."""
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

    if is_blocked(page):
        raise AntiBotBlockedError("Kalibrr served a bot challenge instead of "
                                  "the job board.")

    _apply_search(page, keyword)

    if debug:
        save_debug_html(page, f"kalibrr_search_{keyword}")

    unique_listings: dict[str, JobListing] = {}
    seen_cards = _harvest(page, keyword, unique_listings, 0)
    logging.info("[kalibrr] Initial load: %d jobs", len(unique_listings))

    for load_num in range(1, max_loads + 1):
        try:
            if not _click_load_more(page, seen_cards):
                logging.info("[kalibrr] No 'Load More' button after %d load(s)"
                             " — that is all of them.", load_num - 1)
                break
        except PlaywrightTimeoutError:
            logging.info("[kalibrr] 'Load More' added nothing within %dms — "
                         "treating %d jobs as the full set.",
                         config.LOAD_MORE_TIMEOUT_MS, len(unique_listings))
            break
        except Exception as error:
            logging.error("[kalibrr] Failed to load more: %s", error)
            save_error_screenshot(page, "kalibrr_load_more")
            break

        before = len(unique_listings)
        seen_cards = _harvest(page, keyword, unique_listings, seen_cards)
        logging.info("[kalibrr] Load %d/%d: +%d new jobs (total: %d)",
                     load_num, max_loads, len(unique_listings) - before,
                     len(unique_listings))

    if not unique_listings:
        html_path = save_debug_html(page, "kalibrr_no_results")
        logging.warning(
            "[kalibrr] 0 listings extracted — the site may have changed "
            "markup. Inspect %s and update SELECTORS in config.py.", html_path)

    return list(unique_listings.values())


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

    Note: Kalibrr uses "Load More" instead of pagination. max_pages is
    repurposed as the number of "Load More" clicks (default: 5).
    Location parameter is accepted for API compatibility but ignored
    (Kalibrr doesn't support location filtering).
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
                try:
                    listings = _scrape_with_keyword(page, keyword, debug,
                                                    max_loads=max_pages)
                except (SearchUnavailableError, AntiBotBlockedError) as error:
                    # Neither is fixable by trying the next keyword, and both
                    # need to be seen rather than buried among page warnings.
                    logging.error("[kalibrr] %s", error)
                    break

                for listing in listings:
                    if listing.job_key not in unique_listings:
                        unique_listings[listing.job_key] = listing

                if index < len(keywords) - 1:
                    time.sleep(delay_seconds)  # pause between keyword searches too

            logging.info("[kalibrr] Total unique jobs: %d", len(unique_listings))

            if fetch_details and unique_listings:
                fetch_full_descriptions(SOURCE, _SELECTORS, context,
                                        list(unique_listings.values()),
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

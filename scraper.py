"""
scraper.py
Scrapes job listings from JobStreet Philippines (ph.jobstreet.com) for a given
search term using Playwright.

IMPORTANT (please read):
- JobStreet's HTML/selectors change periodically. If this script returns zero
  results, run with --debug to save the page HTML to debug_page.html, open it,
  and update the SELECTORS dict below to match the current markup.
- This scrapes publicly visible search-result pages only (no login, no
  personal data). Keep request volume low and add delays to avoid getting
  rate-limited or blocked. This is intended for personal/non-commercial use.
"""
import argparse
import json
import time
import urllib.parse
from dataclasses import dataclass, asdict

from playwright.sync_api import sync_playwright

BASE_URL = "https://ph.jobstreet.com"

# Centralize selectors here so they're easy to patch when the site changes.
SELECTORS = {
    "job_card": "article",
    "job_title": "a[data-automation='jobTitle']",
    "job_company": "a[data-automation='jobCompany'], span[data-automation='jobCompany']",
    "job_location": "span[data-automation='jobLocation']",
    "job_teaser": "span[data-automation='jobShortDescription']",
}


@dataclass
class JobListing:
    title: str
    company: str
    location: str
    teaser: str
    url: str


def build_search_url(keyword: str, page_num: int = 1) -> str:
    slug = urllib.parse.quote(keyword.strip().lower().replace(" ", "-"))
    url = f"{BASE_URL}/{slug}-jobs"
    if page_num > 1:
        url += f"?page={page_num}"
    return url


def scrape_search_page(page, keyword: str, page_num: int, debug: bool = False) -> list[JobListing]:
    url = build_search_url(keyword, page_num)
    print(f"  Fetching: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # Give the page a moment for JS-rendered content to settle.
    page.wait_for_timeout(2000)

    if debug:
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        print("  Saved debug_page.html")

    cards = page.query_selector_all(SELECTORS["job_card"])
    listings = []
    for card in cards:
        title_el = card.query_selector(SELECTORS["job_title"])
        if not title_el:
            continue  # not a job card (nav/footer/etc.)

        title = title_el.inner_text().strip()
        href = title_el.get_attribute("href") or ""
        job_url = href if href.startswith("http") else BASE_URL + href

        company_el = card.query_selector(SELECTORS["job_company"])
        location_el = card.query_selector(SELECTORS["job_location"])
        teaser_el = card.query_selector(SELECTORS["job_teaser"])

        listings.append(JobListing(
            title=title,
            company=company_el.inner_text().strip() if company_el else "",
            location=location_el.inner_text().strip() if location_el else "",
            teaser=teaser_el.inner_text().strip() if teaser_el else "",
            url=job_url,
        ))

    return listings


def scrape_jobs(keyword: str, max_pages: int = 2, delay_seconds: float = 3.0,
                 debug: bool = False) -> list[JobListing]:
    all_listings = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not debug)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = context.new_page()

        for page_num in range(1, max_pages + 1):
            listings = scrape_search_page(page, keyword, page_num, debug=debug)
            if not listings:
                print(f"  No listings found on page {page_num}, stopping.")
                break
            all_listings.extend(listings)
            print(f"  Page {page_num}: {len(listings)} listings")
            time.sleep(delay_seconds)  # be polite, avoid rate limits

        browser.close()

    return all_listings


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape JobStreet PH job listings")
    parser.add_argument("keyword", help="Job title/keyword to search, e.g. 'python developer'")
    parser.add_argument("--pages", type=int, default=2, help="Number of search-result pages to scrape")
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds to wait between page requests")
    parser.add_argument("--debug", action="store_true", help="Run visibly and save debug_page.html")
    parser.add_argument("--out", default="jobs_raw.json", help="Output JSON file")
    args = parser.parse_args()

    results = scrape_jobs(args.keyword, max_pages=args.pages, delay_seconds=args.delay, debug=args.debug)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)

    print(f"\nTotal listings scraped: {len(results)}")
    print(f"Saved to {args.out}")

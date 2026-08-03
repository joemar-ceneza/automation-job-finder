"""
selector_health.py
Reports which of each site's selectors still match a live page.

Selector rot is the failure this tool is most exposed to: the scrapers depend
on markup nobody here controls, and when it changes the symptom is a run that
quietly returns nothing at 6am. A scheduled job that finds zero listings looks
exactly like a day with no new jobs.

So this is a command rather than a test — it needs the network, it is run on
demand, and its output is a report you read rather than a pass/fail nobody
sees. It answers one question per selector: does this still match anything?
"""
import logging

from playwright.sync_api import sync_playwright

import config
import scraper_common

# Selectors that only exist on a detail page, and interactive controls that may
# legitimately be absent from a first page of results (there is no "Load More"
# until there is more to load). Reported, but never counted as a failure.
_NOT_ON_SEARCH_PAGE = {"job_detail_description", "job_detail_salary"}
_OPTIONAL = {"next_button", "load_more", "search_input", "job_title_badge"}

_PROBE_KEYWORD = "developer"


def _section(title: str) -> None:
    logging.info("=" * 70)
    logging.info(title)
    logging.info("=" * 70)


# ======================================================
# PROBING ONE SITE
# ======================================================
def _count_matches(page, selector: str | None) -> int | None:
    """How many elements a selector matches, or None when it isn't set."""
    if not selector:
        return None
    try:
        return len(page.query_selector_all(selector))
    except Exception as error:              # an invalid selector is a finding
        logging.debug("Selector %r could not be evaluated: %s", selector, error)
        return -1


def _probe_site(context, site: str, module) -> dict:
    """Loads one search page for a site and counts every selector's matches."""
    selectors = config.SELECTORS[site]
    result = {"site": site, "url": "", "blocked": False, "error": "",
              "counts": {}}
    page = context.new_page()
    try:
        result["url"] = module.probe_url(_PROBE_KEYWORD)
        page.goto(result["url"], wait_until="domcontentloaded",
                  timeout=config.PAGE_LOAD_TIMEOUT_MS)
        page.wait_for_timeout(config.RENDER_WAIT_MS)

        if scraper_common.is_blocked(page):
            # Worth separating: every selector will read zero, and none of them
            # are the reason.
            result["blocked"] = True
            return result

        for name, selector in selectors.items():
            result["counts"][name] = _count_matches(page, selector)
    except Exception as error:
        result["error"] = str(error)
    finally:
        page.close()
    return result


# ======================================================
# REPORTING
# ======================================================
def _verdict(name: str, count: int | None) -> str:
    if count is None:
        return "not set"
    if count == -1:
        return "INVALID"
    if count > 0:
        return f"{count} match(es)"
    if name in _NOT_ON_SEARCH_PAGE:
        return "0 — detail page only, not checked here"
    if name in _OPTIONAL:
        return "0 — optional, may be absent"
    return "0 — BROKEN"


def _is_broken(name: str, count: int | None) -> bool:
    if count is None or name in _NOT_ON_SEARCH_PAGE or name in _OPTIONAL:
        return False
    return count <= 0


def _report_site(result: dict) -> int:
    """Logs one site's findings. Returns the number of broken selectors."""
    site = result["site"]
    logging.info("-" * 70)
    logging.info("%s — %s", site, result["url"])

    if result["blocked"]:
        logging.warning("  Blocked by anti-bot verification — no selector "
                        "could be checked. This is not a markup problem.")
        return 0
    if result["error"]:
        logging.error("  Could not load the page: %s", result["error"])
        return 0

    broken = 0
    for name, count in result["counts"].items():
        verdict = _verdict(name, count)
        if _is_broken(name, count):
            broken += 1
        logging.info(f"  {name:<24}: {verdict}")

    if broken:
        logging.warning("  %d selector(s) match nothing — update "
                        "SELECTORS['%s'] in config.py.", broken, site)
    else:
        logging.info("  All checkable selectors still match.")
    return broken


# ======================================================
# PUBLIC ENTRY POINT
# ======================================================
def run_check(sites: dict) -> int:
    """
    Probes one search page per site and reports selector health.
    `sites` maps a site name to its scraper module.
    Returns the total number of broken selectors across all sites.
    """
    _section("SELECTOR HEALTH CHECK")
    logging.info("Loading one search page per site with the keyword %r. "
                 "This makes a small number of real requests.", _PROBE_KEYWORD)

    total_broken = 0
    browser = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=config.HEADLESS)
            context = browser.new_context(user_agent=config.USER_AGENT)
            for site, module in sites.items():
                if not hasattr(module, "probe_url"):
                    logging.warning("%s has no probe_url() — skipping.", site)
                    continue
                total_broken += _report_site(_probe_site(context, site, module))
        finally:
            if browser:
                browser.close()

    _section("SUMMARY")
    if total_broken:
        logging.warning("%d selector(s) need attention. Each one is a field "
                        "that will silently come back empty until it is fixed.",
                        total_broken)
    else:
        logging.info("Every checkable selector still matches.")
    return total_broken

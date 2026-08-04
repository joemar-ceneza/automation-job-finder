"""
Extraction tested against saved HTML, per ARCHITECTURE section 11.

Everything else in this suite tests logic against fakes, which proves the code
does what it means to — but never that a selector matches anything real. That
gap is not theoretical: `jobstreet.job_location` was pinned to a <span> that had
become an <a>, and it silently blanked the location on all 643 JobStreet rows
in the database. No amount of fake-based testing would have caught it, and
--check-selectors only catches it if someone runs it.

So these run each scraper's real `_extract_listing` over a real page captured
from each site, and assert the fields actually come out. When a site changes its
markup, this fails on your machine at commit time instead of quietly emptying a
column for months.

The fixtures in tests/fixtures/ are captured deliberately and then left alone.
Nothing here touches the network. A browser is used only to parse saved HTML —
`page.set_content()`, never `page.goto()` — because the selectors are
Playwright-flavoured and must be evaluated by the same engine that runs in
production.

To refresh a fixture after a genuine site change, re-capture it from a page you
have confirmed is working, then update the expectations below if they moved.
"""
import gzip
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import scraper_common
import scraper_indeed
import scraper_jobstreet
import scraper_kalibrr
import scraper_linkedin
import scraper_onlinejobs

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

SCRAPERS = {
    "jobstreet": scraper_jobstreet,
    "onlinejobs": scraper_onlinejobs,
    "kalibrr": scraper_kalibrr,
    "linkedin": scraper_linkedin,
    "indeed": scraper_indeed,
}

# Fields that must come out non-empty for a majority of cards on each site.
# Deliberately not "every card": a real page legitimately has ads without a
# salary, and employers who post anonymously. These are the fields whose
# emptiness would mean the selector is broken rather than the data absent.
REQUIRED_FIELDS = {
    "jobstreet": ["title", "company", "location", "url"],
    "onlinejobs": ["title", "url"],          # employer hidden on search cards
    "kalibrr": ["title", "url"],
    "linkedin": ["title", "company", "location", "url"],
    "indeed": ["title", "company", "location", "url"],
}


def load_fixture(site: str) -> str:
    path = FIXTURES / f"{site}_search.html.gz"
    if not path.exists():
        pytest.skip(f"no saved fixture for {site} — capture one first")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="session")
def browser():
    """One browser for the whole module — launching per test is far too slow."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        instance = p.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture(scope="session")
def pages(browser):
    """A parsed page per site, from saved HTML. Never navigates anywhere."""
    context = browser.new_context()
    loaded = {}
    for site in SCRAPERS:
        path = FIXTURES / f"{site}_search.html.gz"
        if not path.exists():
            continue
        page = context.new_page()
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            page.set_content(handle.read())
        loaded[site] = page
    yield loaded
    context.close()


def cards_for(pages, site):
    if site not in pages:
        pytest.skip(f"no saved fixture for {site}")
    # Through query_all, because a selector entry may be a list of fallbacks —
    # the same path production takes.
    return scraper_common.query_all(pages[site],
                                    config.SELECTORS[site]["job_card"])


# ======================================================
# THE CARD SELECTOR ITSELF
# ======================================================
@pytest.mark.parametrize("site", list(SCRAPERS))
def test_the_card_selector_finds_cards(pages, site):
    """If this fails, nothing downstream can work."""
    assert len(cards_for(pages, site)) > 0


# ======================================================
# EXTRACTION
# ======================================================
@pytest.mark.parametrize("site", list(SCRAPERS))
def test_listings_are_extracted_from_a_real_page(pages, site):
    cards = cards_for(pages, site)
    listings = [SCRAPERS[site]._extract_listing(card, "developer")
                for card in cards]
    found = [listing for listing in listings if listing]
    assert len(found) >= max(1, len(cards) // 2), (
        f"{site}: only {len(found)} of {len(cards)} cards yielded a listing")


@pytest.mark.parametrize("site", list(SCRAPERS))
def test_required_fields_are_populated(pages, site):
    """
    The regression that started this: a selector that matches nothing produces
    an empty field, not an error, so it survives every other kind of test.
    """
    cards = cards_for(pages, site)
    listings = [SCRAPERS[site]._extract_listing(card, "developer")
                for card in cards]
    found = [listing for listing in listings if listing]
    assert found, f"{site}: no listings extracted at all"

    for field in REQUIRED_FIELDS[site]:
        filled = sum(1 for listing in found if getattr(listing, field))
        assert filled > len(found) // 2, (
            f"{site}.{field} is empty on {len(found) - filled} of "
            f"{len(found)} listings — SELECTORS['{site}']['job_{field}'] "
            f"most likely no longer matches")


@pytest.mark.parametrize("site", list(SCRAPERS))
def test_every_listing_gets_a_stable_key(pages, site):
    cards = cards_for(pages, site)
    found = [listing for card in cards
             if (listing := SCRAPERS[site]._extract_listing(card, "developer"))]
    assert all(listing.job_key.startswith(f"{site}:") for listing in found)
    # Distinct ads must not collapse onto one key.
    assert len({listing.job_key for listing in found}) == len(found)


@pytest.mark.parametrize("site", list(SCRAPERS))
def test_keys_come_from_the_site_id_not_the_title_fallback(pages, site):
    """
    make_job_key() falls back to title+company when the id regex misses, and
    that fallback is lossy: one employer posting the same title twice produces
    one key for two different jobs, so the second is silently discarded.

    Because the fallback still yields a plausible-looking key, a broken id
    pattern shows up as nothing at all — LinkedIn's matched 0 of 61 URLs and
    the only visible symptom was two missing IBM listings.
    """
    cards = cards_for(pages, site)
    found = [listing for card in cards
             if (listing := SCRAPERS[site]._extract_listing(card, "developer"))]
    by_id = [listing for listing in found if ":id:" in listing.job_key]
    assert len(by_id) > len(found) // 2, (
        f"{site}: only {len(by_id)} of {len(found)} listings got a real site "
        f"id — _JOB_ID_PATTERN most likely no longer matches this site's URLs")


@pytest.mark.parametrize("site", list(SCRAPERS))
def test_urls_are_absolute(pages, site):
    cards = cards_for(pages, site)
    found = [listing for card in cards
             if (listing := SCRAPERS[site]._extract_listing(card, "developer"))]
    assert all(listing.url.startswith("http") for listing in found)


@pytest.mark.parametrize("site", list(SCRAPERS))
def test_the_search_keyword_is_recorded(pages, site):
    cards = cards_for(pages, site)
    found = [listing for card in cards
             if (listing := SCRAPERS[site]._extract_listing(card, "developer"))]
    assert all(listing.search_keyword == "developer" for listing in found)


# ======================================================
# THE SPECIFIC REGRESSION
# ======================================================
def test_jobstreet_location_is_not_blank(pages):
    """
    Pinned deliberately. This exact field was silently empty on all 643 stored
    JobStreet jobs because the selector named a <span> after the markup had
    moved to an <a>. It must never regress unnoticed again.
    """
    cards = cards_for(pages, "jobstreet")
    found = [listing for card in cards
             if (listing := scraper_jobstreet._extract_listing(card, "developer"))]
    with_location = [listing for listing in found if listing.location]
    assert len(with_location) == len(found), (
        f"{len(found) - len(with_location)} of {len(found)} JobStreet "
        f"listings have no location")


def test_jobstreet_selectors_are_not_pinned_to_a_tag():
    """
    The fix was to match on data-automation alone. Re-adding a tag qualifier is
    what broke it, so keep that decision enforced rather than remembered.
    """
    for name, selector in config.SELECTORS["jobstreet"].items():
        for candidate in scraper_common.candidates(selector):
            if not candidate.startswith("["):
                continue
            assert candidate.startswith("[data-"), (
                f"jobstreet.{name} candidate {candidate!r} should match on the "
                f"attribute alone, not on a tag")

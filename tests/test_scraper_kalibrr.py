"""
Tests for the Kalibrr scraper's two site-specific hazards.

Kalibrr filters by typing into a search box rather than by URL. That makes a
missing search box uniquely dangerous: the page still renders perfectly, just
unfiltered, so a warn-and-continue would quietly attribute the entire job board
to whatever keyword you searched for. `search_keyword` is the column that
salary banding, skill demand and --learn all group by, so that mislabelling
would not surface as an error — it would surface months later as analytics
nobody can explain.

The second hazard is paging: "Load More" replaces the card list in place, so
re-parsing every card after each click is both quadratic and a source of
double-counting.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper_kalibrr


class FakeCard:
    def __init__(self, title, company, href):
        self._fields = {"h2 a, h3 a": title, "span.k-text-subdued": company}
        self._href = href

    def query_selector(self, selector):
        if "href" in selector:
            return FakeLink(self._href)
        text = self._fields.get(selector)
        return FakeText(text) if text else None

    def inner_text(self):
        return "\n".join(v for v in self._fields.values() if v)


class FakeText:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class FakeLink:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, name):
        return self._href


class FakePage:
    def __init__(self, cards=(), has_search=True):
        self.cards = list(cards)
        self.has_search = has_search
        self.filled = None

    def query_selector(self, selector):
        if selector == scraper_kalibrr._SELECTORS["search_input"]:
            return FakeInput(self) if self.has_search else None
        return None

    def query_selector_all(self, selector):
        return self.cards

    def keyboard_press(self, key):
        pass

    @property
    def keyboard(self):
        return self

    def press(self, key):
        pass

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return "<html>jobs</html>"

    def title(self):
        return "Kalibrr jobs"


class FakeInput:
    def __init__(self, page):
        self._page = page

    def fill(self, value):
        self._page.filled = value


# ======================================================
# THE SEARCH BOX MUST BE FATAL WHEN MISSING
# ======================================================
def test_a_missing_search_box_raises_instead_of_scraping_everything(monkeypatch):
    monkeypatch.setattr(scraper_kalibrr, "save_debug_html",
                        lambda page, label: "logs/fake.html")
    page = FakePage(has_search=False)

    with pytest.raises(scraper_kalibrr.SearchUnavailableError) as caught:
        scraper_kalibrr._apply_search(page, "python developer")

    message = str(caught.value)
    assert "unfiltered" in message          # says why it refused
    assert "search_input" in message        # says what to fix


def test_the_error_names_the_keyword_that_could_not_be_applied(monkeypatch):
    monkeypatch.setattr(scraper_kalibrr, "save_debug_html",
                        lambda page, label: "logs/fake.html")
    with pytest.raises(scraper_kalibrr.SearchUnavailableError) as caught:
        scraper_kalibrr._apply_search(FakePage(has_search=False), "django")
    assert "django" in str(caught.value)


def test_a_present_search_box_is_filled():
    page = FakePage(has_search=True)
    scraper_kalibrr._apply_search(page, "python developer")
    assert page.filled == "python developer"


def test_an_empty_keyword_is_not_an_error():
    page = FakePage(has_search=False)
    scraper_kalibrr._apply_search(page, "")     # must not raise
    assert page.filled is None


# ======================================================
# CARD EXTRACTION
# ======================================================
def test_title_and_company_come_from_selectors_not_line_order():
    """
    Reading lines[0]/lines[1] breaks the moment a card carries a "Featured"
    badge, which files the real title as the company.
    """
    card = FakeCard("Python Developer", "Acme Inc",
                    "https://www.kalibrr.com/c/acme/jobs/12345/python-dev")
    listing = scraper_kalibrr._extract_listing(card, "python")
    assert listing.title == "Python Developer"
    assert listing.company == "Acme Inc"
    assert listing.job_key == "kalibrr:id:12345"


def test_the_search_keyword_is_recorded_on_every_listing():
    card = FakeCard("Dev", "Acme",
                    "https://www.kalibrr.com/c/a/jobs/999/dev")
    assert scraper_kalibrr._extract_listing(card, "django").search_keyword == "django"


def test_a_card_without_a_link_is_skipped():
    class Linkless(FakeCard):
        def query_selector(self, selector):
            return None if "href" in selector else super().query_selector(selector)

    assert scraper_kalibrr._extract_listing(
        Linkless("T", "C", ""), "python") is None


# ======================================================
# LOAD MORE
# ======================================================
def test_harvest_only_parses_cards_it_has_not_seen():
    """
    Re-parsing the whole list after every click is quadratic. Harvest returns
    the new high-water mark so each card is read exactly once.
    """
    cards = [FakeCard(f"Job {n}", "Acme",
                      f"https://www.kalibrr.com/c/a/jobs/{n}/job")
             for n in range(5)]
    page = FakePage(cards=cards)
    found = {}

    seen = scraper_kalibrr._harvest(page, "python", found, 0)
    assert seen == 5
    assert len(found) == 5

    # A second pass with nothing appended must add nothing.
    seen = scraper_kalibrr._harvest(page, "python", found, seen)
    assert seen == 5
    assert len(found) == 5


def test_harvest_picks_up_appended_cards():
    cards = [FakeCard(f"Job {n}", "Acme",
                      f"https://www.kalibrr.com/c/a/jobs/{n}/job")
             for n in range(3)]
    page = FakePage(cards=cards)
    found = {}
    seen = scraper_kalibrr._harvest(page, "python", found, 0)

    page.cards.extend([
        FakeCard(f"Job {n}", "Acme",
                 f"https://www.kalibrr.com/c/a/jobs/{n}/job")
        for n in range(3, 6)])

    seen = scraper_kalibrr._harvest(page, "python", found, seen)
    assert seen == 6
    assert len(found) == 6

"""
Tests for the shared scraper building blocks.

Two behaviours here are worth more than the rest put together:

  - A wrong detail selector must fail *fast*. It is a deterministic failure —
    no retry can make a selector match — so retrying it costs
    RETRY_ATTEMPTS x DETAIL_WAIT_TIMEOUT_MS plus backoff on every single
    listing, which is roughly 39 seconds each with the shipped defaults. On a
    hundred jobs that is an hour of dead waiting whose only symptom is a wall
    of identical warnings.

  - A bot challenge must be told apart from a markup change. Both produce zero
    cards, but one is fixed by editing selectors and the other cannot be fixed
    at all, so reporting the wrong one sends you down a dead end.

Nothing here touches a browser or a network.
"""
import os
import sys

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import scraper_common

SELECTORS = {"job_detail_description": "div#desc",
             "job_detail_salary": "span.pay"}


# ======================================================
# FAKES
# ======================================================
class FakeElement:
    def __init__(self, text=""):
        self._text = text

    def inner_text(self):
        return self._text


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    """A page whose behaviour is dictated per-instance by the context."""

    def __init__(self, behaviour="ok", title="Job", body="a job advert"):
        self.behaviour = behaviour
        self._title = title
        self._body = body
        self.closed = False

    def goto(self, url, **kwargs):
        return FakeResponse(404 if self.behaviour == "gone" else 200)

    def title(self):
        return self._title

    def content(self):
        return self._body

    def wait_for_selector(self, selector, **kwargs):
        if self.behaviour == "timeout":
            raise PlaywrightTimeoutError(f"no match for {selector}")

    def query_selector(self, selector):
        if self.behaviour != "ok":
            return None
        return FakeElement("full description" if "desc" in selector else "50k")

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.pages = []

    def new_page(self):
        page = FakePage(self.behaviour)
        self.pages.append(page)
        return page


def listings(count: int) -> list[scraper_common.JobListing]:
    return [scraper_common.JobListing(
        job_key=f"k{n}", title=f"Job {n}", company="Acme", location="",
        teaser="teaser", url=f"https://example.com/{n}") for n in range(count)]


# ======================================================
# THE CIRCUIT BREAKER
# ======================================================
def test_a_wrong_detail_selector_stops_the_pass_early():
    """
    The whole point: with a selector that never matches, we must give up after
    DETAIL_FAILURE_LIMIT jobs instead of grinding through all fifty.
    """
    context = FakeContext("timeout")
    jobs = listings(50)

    scraper_common.fetch_full_descriptions(
        "testsite", SELECTORS, context, jobs, delay_seconds=0)

    assert len(context.pages) == config.DETAIL_FAILURE_LIMIT


def test_it_does_not_retry_a_selector_timeout():
    """
    Retrying a selector that does not match cannot succeed. One page per job,
    not RETRY_ATTEMPTS pages per job.
    """
    context = FakeContext("timeout")
    scraper_common.fetch_full_descriptions(
        "testsite", SELECTORS, context, listings(1), delay_seconds=0)
    assert len(context.pages) == 1


def test_teasers_survive_a_failed_detail_pass():
    """A failure must not blank out data the search page already gave us."""
    context = FakeContext("timeout")
    jobs = listings(3)
    scraper_common.fetch_full_descriptions(
        "testsite", SELECTORS, context, jobs, delay_seconds=0)
    assert all(job.teaser == "teaser" for job in jobs)
    assert all(job.description == "" for job in jobs)


def test_a_healthy_site_fills_every_description():
    context = FakeContext("ok")
    jobs = listings(4)
    scraper_common.fetch_full_descriptions(
        "testsite", SELECTORS, context, jobs, delay_seconds=0)
    assert all(job.description == "full description" for job in jobs)
    assert len(context.pages) == 4


def test_a_removed_ad_is_not_a_selector_failure():
    """
    A taken-down ad is normal and per-job, so it must neither be retried nor
    count toward the circuit breaker — otherwise five stale ads in a row would
    abort a perfectly healthy run.
    """
    context = FakeContext("gone")
    jobs = listings(10)
    scraper_common.fetch_full_descriptions(
        "testsite", SELECTORS, context, jobs, delay_seconds=0)
    assert len(context.pages) == 10


def test_every_page_is_closed():
    context = FakeContext("timeout")
    scraper_common.fetch_full_descriptions(
        "testsite", SELECTORS, context, listings(10), delay_seconds=0)
    assert all(page.closed for page in context.pages)


def test_a_missing_salary_selector_is_not_an_error():
    """onlinejobs has no detail salary; the shared helper must cope."""
    context = FakeContext("ok")
    jobs = listings(1)
    scraper_common.fetch_full_descriptions(
        "testsite", {"job_detail_description": "div#desc"}, context, jobs,
        delay_seconds=0)
    assert jobs[0].description == "full description"
    assert jobs[0].salary == ""


# ======================================================
# ANTI-BOT DETECTION
# ======================================================
@pytest.mark.parametrize("title,body", [
    ("Just a moment...", "<html>checking your browser</html>"),
    ("Security check", "please verify you are human"),
    ("Attention Required", "<div id=cf-challenge></div>"),
    ("Blocked", "Access Denied"),
])
def test_a_challenge_page_is_recognised(title, body):
    assert scraper_common.is_blocked(FakePage(title=title, body=body)) is True


def test_an_ordinary_results_page_is_not_a_challenge():
    page = FakePage(title="python developer jobs",
                    body="<div class=job>Python Developer at Acme</div>")
    assert scraper_common.is_blocked(page) is False


def test_a_torn_down_page_does_not_raise():
    class Broken:
        def title(self):
            raise RuntimeError("page closed")

        def content(self):
            return ""

    assert scraper_common.is_blocked(Broken()) is False


# ======================================================
# FIELD EXTRACTION
# ======================================================
def test_a_none_selector_yields_an_empty_field():
    """
    Kalibrr gives several fields the same utility class. Declaring those None
    is deliberate: an empty field is honest, a colliding one is quietly wrong.
    """
    card = FakeElement()
    assert scraper_common.text_from(card, None) == ""


def test_a_missing_element_yields_an_empty_field():
    class Card:
        def query_selector(self, selector):
            return None

    assert scraper_common.text_from(Card(), "span.nope") == ""


def test_text_is_stripped():
    class Card:
        def query_selector(self, selector):
            return FakeElement("  Makati  ")

    assert scraper_common.text_from(Card(), "span.loc") == "Makati"


# ======================================================
# SELECTOR FALLBACKS
# ======================================================
class Recorder:
    """A card that only answers to one selector, and logs what was tried."""

    def __init__(self, works: str, text: str = "Makati"):
        self.works = works
        self.text = text
        self.tried: list[str] = []

    def query_selector(self, selector):
        self.tried.append(selector)
        return FakeElement(self.text) if selector == self.works else None

    def query_selector_all(self, selector):
        self.tried.append(selector)
        return [FakeElement(self.text)] if selector == self.works else []


def test_a_single_selector_still_works():
    assert scraper_common.candidates("div.a") == ["div.a"]


def test_a_list_becomes_candidates_in_order():
    assert scraper_common.candidates(["div.a", "div.b"]) == ["div.a", "div.b"]


def test_none_and_empty_yield_no_candidates():
    assert scraper_common.candidates(None) == []
    assert scraper_common.candidates([]) == []
    assert scraper_common.candidates([None, ""]) == []


def test_the_first_matching_candidate_wins():
    card = Recorder(works="div.first")
    assert scraper_common.text_from(card, ["div.first", "div.second"]) == "Makati"
    assert card.tried == ["div.first"]      # stops as soon as one matches


def test_a_fallback_rescues_a_rotted_primary():
    """
    The whole point. When JobStreet moved job_location from a <span> to an <a>,
    a second candidate would have carried the field through untouched instead of
    blanking it on 643 rows.
    """
    card = Recorder(works="a[data-automation='jobLocation']")
    found = scraper_common.text_from(
        card, ["span[data-automation='jobLocation']",
               "a[data-automation='jobLocation']"])
    assert found == "Makati"
    assert len(card.tried) == 2             # tried the primary first


def test_all_candidates_missing_yields_empty():
    card = Recorder(works="div.nothing-matches-this")
    assert scraper_common.text_from(card, ["div.a", "div.b"]) == ""
    assert card.tried == ["div.a", "div.b"]


def test_query_all_falls_back_too():
    page = Recorder(works="[data-testid='job-card']")
    found = scraper_common.query_all(page, ["article", "[data-testid='job-card']"])
    assert len(found) == 1


def test_an_invalid_candidate_does_not_stop_the_rest():
    """A typo in one fallback must not take the working ones down with it."""
    class Fussy:
        def query_selector(self, selector):
            if selector == "!!bad!!":
                raise ValueError("invalid selector")
            return FakeElement("Cebu")

    assert scraper_common.text_from(Fussy(), ["!!bad!!", "div.ok"]) == "Cebu"


def test_element_from_returns_the_element_not_its_text():
    card = Recorder(works="a.link")
    assert isinstance(scraper_common.element_from(card, ["a.link"]), FakeElement)


def test_a_colliding_date_selector_degrades_to_empty_not_garbage():
    """
    Feeding a company name to the date parser must not invent a date — this is
    what makes a wrong selector show up as a blank field rather than as
    plausible-looking bad data in the database.
    """
    assert scraper_common.parse_relative_date("Acme Digital Inc") == ""

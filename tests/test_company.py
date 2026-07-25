"""
Tests for company intelligence — the honest subset.

Every figure must be arithmetic over postings you scraped. The AI reading may
only infer from the employer's own adverts: a model that "remembers" a
company's headcount or founding year from training data is exactly the
unverifiable intelligence this feature refuses to ship.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_company
import company
from llm import LLMResponse, LLMUnavailable, NullProvider

ADVERT = ("We are a small team building payment tools. You will own features "
          "end to end. Expect a take-home exercise then a technical call.")


def posting(title="Python Developer", first_seen="2026-01-10",
            low=50000, high=70000, **overrides) -> dict:
    base = {"job_key": f"jobstreet:id:{title}:{first_seen}", "title": title,
            "company": "Acme", "location": "Manila", "salary_min": low,
            "salary_max": high, "work_arrangement": "Remote",
            "score_percent": 40.0, "first_seen": first_seen,
            "last_seen": first_seen, "description": ADVERT}
    return {**base, **overrides}


# ======================================================
# THE DETERMINISTIC PROFILE
# ======================================================
def test_an_empty_history_says_so():
    result = company.profile("Acme", [])
    assert result.postings == 0
    assert any("Nothing tracked" in line for line in result.lines)


def test_postings_and_dates_are_counted():
    result = company.profile("Acme", [
        posting(first_seen="2026-01-10"), posting(first_seen="2026-03-11")])
    assert result.postings == 2
    assert result.first_seen == "2026-01-10"
    assert result.last_seen == "2026-03-11"
    assert result.days_active == 60


def test_a_repeated_role_is_surfaced():
    result = company.profile("Acme", [posting(first_seen="2026-01-10"),
                                      posting(first_seen="2026-02-10"),
                                      posting(title="Designer")])
    assert ("Python Developer", 2) in result.repeated_roles
    assert any("Repeatedly hiring" in line for line in result.lines)


def test_posting_rate_is_withheld_over_a_short_span():
    """A rate from under a month reflects when you started scraping, not them."""
    result = company.profile("Acme", [posting(first_seen="2026-01-10"),
                                      posting(first_seen="2026-01-20")])
    assert result.posts_per_month is None


def test_posting_rate_is_computed_over_a_long_span():
    result = company.profile("Acme", [posting(first_seen="2026-01-01"),
                                      posting(first_seen="2026-03-02")])
    assert result.posts_per_month is not None and result.posts_per_month > 0


def test_average_salary_is_computed_from_stated_pay_only():
    result = company.profile("Acme", [
        posting(low=40000, high=60000),                 # midpoint 50k
        posting(low=None, high=None, title="Designer"),  # no pay stated
    ])
    assert result.salaried_postings == 1
    assert result.average_salary == 50000


def test_no_stated_salary_is_reported_plainly():
    result = company.profile("Acme", [posting(low=None, high=None)])
    assert result.average_salary is None
    assert any("state a salary" in line for line in result.lines)


def test_the_unobtainable_data_is_declared_absent():
    """Ratings and headcount are cut on purpose — say so rather than imply."""
    result = company.profile("Acme", [posting()])
    assert any("ratings" in line and "absent" in line for line in result.lines)


# ======================================================
# THE AI READING
# ======================================================
GOOD = {
    "culture": "A small team where you own features end to end.",
    "interview_process": "A take-home exercise then a technical call.",
    "advantages": ["Ownership of whole features"],
    "concerns": ["Small team may mean broad on-call"],
}


class FakeProvider:
    name = "fake"

    def __init__(self, data):
        self._data = data

    def is_available(self):
        return True

    def complete(self, request):
        return LLMResponse(data=self._data, model="fake-1")


class FailingProvider:
    name = "failing"

    def is_available(self):
        return True

    def complete(self, request):
        raise LLMUnavailable("outage")


def base_profile():
    return company.profile("Acme", [posting()])


def test_no_provider_keeps_the_profile():
    result = ai_company.enrich(base_profile(), [posting()], NullProvider())
    assert result.ai_used is False
    assert result.base.postings == 1


def test_a_failing_provider_records_the_reason():
    result = ai_company.enrich(base_profile(), [posting()], FailingProvider())
    assert result.ai_used is False
    assert "outage" in result.note


def test_no_adverts_short_circuits():
    result = ai_company.enrich(company.profile("Acme", []), [],
                               FakeProvider(GOOD))
    assert result.ai_used is False
    assert result.note


def test_a_grounded_reading_is_attached():
    result = ai_company.enrich(base_profile(), [posting()], FakeProvider(GOOD))
    assert result.ai_used is True
    assert "own features" in result.culture
    assert result.advantages and result.concerns


def test_a_remembered_headcount_refuses_the_reading():
    """
    The specific failure this guards: a model supplying a figure it was never
    shown. The adverts never mention 500 of anything.
    """
    invented = dict(GOOD, culture="A 500-person firm founded in 1998.")
    result = ai_company.enrich(base_profile(), [posting()],
                               FakeProvider(invented))
    assert result.ai_used is False
    assert "adverts do not contain" in result.note


def test_an_invented_advantage_is_dropped_but_the_reading_survives():
    invented = dict(GOOD, advantages=["Backed by 40 million in funding",
                                      "Ownership of whole features"])
    result = ai_company.enrich(base_profile(), [posting()],
                               FakeProvider(invented))
    assert result.ai_used is True
    assert result.advantages == ["Ownership of whole features"]

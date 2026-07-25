"""
Tests for the AI salary reading.

Like the rest of the AI layer it enriches the deterministic assessment and never
replaces its numbers. No salary, no provider, or a failure leaves the band
standing and records why.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_salary
import salary_bands
from llm import LLMResponse, LLMUnavailable, NullProvider


def job(low=50000, high=70000, **overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Developer",
            "search_keyword": "python developer", "salary_min": low,
            "salary_max": high, "description": "Backend role.",
            "required_years": 3}
    return {**base, **overrides}


def wide_corpus():
    return [{"salary_min": mid, "salary_max": mid, "search_keyword":
             "python developer"} for mid in range(30000, 90001, 1500)]


GOOD = {
    "competitiveness": "Solidly mid-market for this role.",
    "negotiation": "Anchor at 75k given your experience.",
    "seniority_read": "Fair for a 3-year role.",
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


def base(j=None):
    return salary_bands.assess(j or job(), wide_corpus())


# ======================================================
# FALLBACK
# ======================================================
def test_no_provider_keeps_the_assessment():
    result = ai_salary.enrich(job(), base(), NullProvider())
    assert result.ai_used is False
    assert result.base.has_salary


def test_no_salary_short_circuits():
    no_pay = job(low=None, high=None)
    result = ai_salary.enrich(no_pay, salary_bands.assess(no_pay, wide_corpus()),
                              FakeProvider(GOOD))
    assert result.ai_used is False
    assert "no salary" in result.note.lower()


def test_a_failing_provider_records_the_reason():
    result = ai_salary.enrich(job(), base(), FailingProvider())
    assert result.ai_used is False
    assert "outage" in result.note


def test_an_empty_reading_falls_back():
    result = ai_salary.enrich(job(), base(),
                              FakeProvider(dict(GOOD, competitiveness="  ")))
    assert result.ai_used is False
    assert result.note


# ======================================================
# ENRICHMENT
# ======================================================
def test_a_good_reading_is_attached():
    result = ai_salary.enrich(job(), base(), FakeProvider(GOOD))
    assert result.ai_used is True
    assert "mid-market" in result.competitiveness
    assert result.negotiation and result.seniority_read
    assert result.model == "fake-1"


def test_the_prompt_carries_the_computed_band():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    the_base = base()
    ai_salary.enrich(job(), the_base, Capturing(GOOD))
    assert the_base.band in captured["prompt"]
    assert "corpus_median" in captured["prompt"]

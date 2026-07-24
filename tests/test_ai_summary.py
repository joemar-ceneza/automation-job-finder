"""
Tests for the AI job summary.

Like the rest of the AI layer it enriches the deterministic summary and never
replaces its facts. When the provider is absent or fails, the Standard summary
stands and the reason is recorded; the AI reading never repeats a red flag the
rules already caught.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_summary
import summary
from llm import LLMResponse, LLMUnavailable, NullProvider

STRUCTURED = """Responsibilities:
- Build automation tools

Requirements:
- 3+ years of Python
"""


def job(**overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Developer",
            "company": "Acme", "description": STRUCTURED, "salary": "",
            "work_arrangement": "Remote", "required_years": 3}
    return {**base, **overrides}


GOOD = {
    "overview": "A backend Python role focused on internal automation.",
    "pros": ["Remote", "Clear scope"],
    "cons": ["No salary stated"],
    "growth": "Room to grow into a senior automation role.",
    "red_flags": ["Scope is a little vague on team size"],
}


class FakeProvider:
    name = "fake"

    def __init__(self, data):
        self._data = data
        self.calls = 0

    def is_available(self):
        return True

    def complete(self, request):
        self.calls += 1
        return LLMResponse(data=self._data, model="fake-1")


class FailingProvider:
    name = "failing"

    def is_available(self):
        return True

    def complete(self, request):
        raise LLMUnavailable("outage")


def base(**overrides):
    return summary.summarise(job(**overrides))


# ======================================================
# FALLBACK
# ======================================================
def test_no_provider_keeps_the_deterministic_summary():
    result = ai_summary.enrich(job(), base(), NullProvider())
    assert result.ai_used is False
    assert result.base.has_sections()


def test_a_failing_provider_records_the_reason():
    result = ai_summary.enrich(job(), base(), FailingProvider())
    assert result.ai_used is False
    assert "outage" in result.note


def test_an_empty_overview_falls_back():
    result = ai_summary.enrich(job(), base(),
                               FakeProvider(dict(GOOD, overview="  ")))
    assert result.ai_used is False
    assert result.note


# ======================================================
# ENRICHMENT
# ======================================================
def test_a_good_reading_is_attached():
    result = ai_summary.enrich(job(), base(), FakeProvider(GOOD))
    assert result.ai_used is True
    assert "automation" in result.overview
    assert result.pros and result.cons
    assert result.model == "fake-1"


def test_ai_red_flags_do_not_repeat_the_deterministic_ones():
    scam = base(company="", description="Send your CV to fast.hire@gmail.com")
    assert scam.red_flags  # the rule caught the personal-email flag
    # The model echoes that same flag plus a new one; only the new one survives.
    data = dict(GOOD, red_flags=[scam.red_flags[0], "Vague on reporting line"])
    result = ai_summary.enrich(
        job(company="", description="Send your CV to fast.hire@gmail.com"),
        scam, FakeProvider(data))
    assert result.ai_used is True
    assert scam.red_flags[0] not in result.red_flags
    assert any("reporting line" in flag for flag in result.red_flags)


def test_the_prompt_carries_the_extracted_facts():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    ai_summary.enrich(job(), base(), Capturing(GOOD))
    assert "responsibilities" in captured["prompt"]
    assert "Build automation tools" in captured["prompt"]

"""
Tests for the grounded AI explanation.

The property that matters: AI mode enriches the deterministic explanation and
never overrides it. When the provider is absent, fails, or contradicts the
ground truth, the deterministic answer stands.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_explain
from llm import LLMResponse, LLMUnavailable, NullProvider

RESUME_TEXT = ("Python developer. Skills: Python, React.js, PostgreSQL, "
               "Playwright, REST API development.")
RESUME_SKILLS = ["Python", "React.js", "PostgreSQL", "Playwright"]

GOOD_NARRATIVE = {
    "summary": "Strong fit — your Python and automation experience lines up.",
    "strengths": ["Python", "Playwright automation"],
    "weaknesses": ["No Docker experience shown"],
    "advice": "Lead with your automation work.",
    "improvements": ["Containerise a project with Docker"],
}


def job(**overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Automation Engineer",
            "company": "Acme", "score_percent": 70.0,
            "matched_skills": "Python (title), Playwright",
            "description": "Python, Playwright and Docker automation work."}
    return {**base, **overrides}


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
        raise LLMUnavailable("simulated outage")


# ======================================================
# THE DETERMINISTIC BASE IS ALWAYS PRESENT
# ======================================================
def test_no_provider_returns_the_deterministic_explanation():
    result = ai_explain.enrich(job(), RESUME_SKILLS, RESUME_TEXT,
                               NullProvider())
    assert result.ai_used is False
    assert result.base.lines, "the deterministic explanation must still be there"
    assert result.summary == ""


def test_a_failing_provider_falls_back_silently():
    result = ai_explain.enrich(job(), RESUME_SKILLS, RESUME_TEXT,
                               FailingProvider())
    assert result.ai_used is False
    assert result.base.lines


# ======================================================
# A GOOD NARRATIVE IS ATTACHED
# ======================================================
def test_a_good_narrative_is_used():
    result = ai_explain.enrich(job(), RESUME_SKILLS, RESUME_TEXT,
                               FakeProvider(GOOD_NARRATIVE))
    assert result.ai_used is True
    assert "Strong fit" in result.summary
    assert result.improvements == ["Containerise a project with Docker"]
    assert result.model == "fake-1"


def test_the_deterministic_numbers_are_never_replaced():
    """AI text is attached; the score stays whatever arithmetic produced."""
    result = ai_explain.enrich(job(score_percent=70.0), RESUME_SKILLS,
                               RESUME_TEXT, FakeProvider(GOOD_NARRATIVE))
    assert result.base.score_percent == 70.0


# ======================================================
# CONTRADICTION REJECTION (the reason grounding is worth it)
# ======================================================
def test_a_narrative_telling_you_to_learn_what_you_have_is_rejected():
    """
    The candidate has Playwright; a narrative telling them to improve it
    contradicts the ground truth, so the whole narrative is discarded and the
    deterministic explanation stands.
    """
    contradicting = dict(GOOD_NARRATIVE,
                         improvements=["Get more Playwright experience"])
    result = ai_explain.enrich(job(), RESUME_SKILLS, RESUME_TEXT,
                               FakeProvider(contradicting))
    assert result.ai_used is False, "a contradicting narrative must be dropped"


def test_a_narrative_suggesting_a_genuinely_missing_skill_is_kept():
    fine = dict(GOOD_NARRATIVE, improvements=["Learn Docker", "Try AWS"])
    result = ai_explain.enrich(job(), RESUME_SKILLS, RESUME_TEXT,
                               FakeProvider(fine))
    assert result.ai_used is True


# ======================================================
# THE MODEL IS GIVEN FACTS, NOT ASKED TO DERIVE THEM
# ======================================================
def test_the_prompt_carries_the_computed_facts():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            captured["schema"] = request.schema
            return super().complete(request)

    ai_explain.enrich(job(), RESUME_SKILLS, RESUME_TEXT,
                      Capturing(GOOD_NARRATIVE))
    assert "match_percent" in captured["prompt"]
    assert "matched_skills" in captured["prompt"]
    # The schema forbids the model from returning a score at all.
    assert "match_percent" not in captured["schema"]["properties"]


def test_the_cache_salt_is_the_job_key():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["salt"] = request.cache_salt
            return super().complete(request)

    ai_explain.enrich(job(job_key="jobstreet:id:99"), RESUME_SKILLS,
                      RESUME_TEXT, Capturing(GOOD_NARRATIVE))
    assert captured["salt"] == ("jobstreet:id:99",)

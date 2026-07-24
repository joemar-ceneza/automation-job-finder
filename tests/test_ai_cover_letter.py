"""
Tests for AI cover letters.

The load-bearing guarantee is the same as the resume rewriter's: the body is
the model's prose, but a paragraph that invents a number or a skill the resume
does not evidence is caught in code and the whole letter falls back to the
deterministic template — because a letter you send an employer cannot trust the
prompt alone.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_cover_letter
import resume_model
from llm import LLMResponse, LLMUnavailable, NullProvider

RESUME_MD = """# Jane Dev
Python Developer
jane@example.com

## Skills

Python, PostgreSQL, Playwright

## Experience

### Backend Developer — Acme Corp
2021 - Present
- Built internal tooling used by the operations team.
- Wrote Python scripts that cut manual review time by 30%.
"""


def resume():
    return resume_model.parse_markdown(RESUME_MD)


def job(**overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Developer",
            "company": "Globe", "description": "Python and PostgreSQL work."}
    return {**base, **overrides}


class FakeProvider:
    name = "fake"

    def __init__(self, paragraphs):
        self._paragraphs = paragraphs
        self.calls = 0

    def is_available(self):
        return True

    def complete(self, request):
        self.calls += 1
        return LLMResponse(data={"paragraphs": self._paragraphs},
                           model="fake-1")


class FailingProvider:
    name = "failing"

    def is_available(self):
        return True

    def complete(self, request):
        raise LLMUnavailable("outage")


# ======================================================
# FALLBACK TO THE TEMPLATE
# ======================================================
def test_no_provider_returns_the_template_letter():
    letter = ai_cover_letter.compose(resume(), job(), NullProvider())
    assert letter.ai_used is False
    assert letter.paragraphs  # a template letter was still produced


def test_a_failing_provider_falls_back_to_the_template():
    letter = ai_cover_letter.compose(resume(), job(), FailingProvider())
    assert letter.ai_used is False
    assert letter.paragraphs


def test_an_empty_body_falls_back_to_the_template():
    letter = ai_cover_letter.compose(resume(), job(), FakeProvider([]))
    assert letter.ai_used is False
    assert letter.paragraphs


# ======================================================
# THE FABRICATION GUARANTEE
# ======================================================
def test_a_grounded_letter_is_used():
    provider = FakeProvider([
        "I was excited to see your Python Developer role at Globe.",
        "At Acme Corp I built internal tooling for the operations team and "
        "wrote Python scripts that cut manual review time by 30%.",
        "I would welcome the chance to bring that to your team.",
    ])
    letter = ai_cover_letter.compose(resume(), job(), provider)
    assert letter.ai_used is True
    assert letter.model == "fake-1"
    assert any("operations team" in para for para in letter.paragraphs)


def test_a_fabricated_paragraph_discards_the_whole_letter():
    provider = FakeProvider([
        "I was excited to see your Python Developer role at Globe.",
        # invents a metric and a skill the resume does not show
        "I cut costs by 80% by rebuilding the platform on Kubernetes.",
    ])
    letter = ai_cover_letter.compose(resume(), job(), provider)
    assert letter.ai_used is False
    body = " ".join(letter.paragraphs)
    assert "80%" not in body and "Kubernetes" not in body


def test_the_company_name_is_not_mistaken_for_an_invented_number():
    """A number in the company or title is whitelisted, not flagged."""
    provider = FakeProvider([
        "Your role at 3M Philippines is a strong match for my background.",
        "At Acme Corp I wrote Python scripts that cut review time by 30%.",
    ])
    letter = ai_cover_letter.compose(
        resume(), job(company="3M Philippines"), provider)
    assert letter.ai_used is True


# ======================================================
# ENVELOPE IS DETERMINISTIC, INPUT IS UNTOUCHED
# ======================================================
def test_the_envelope_is_copied_from_the_standard_letter():
    provider = FakeProvider(["A grounded paragraph about Python work."])
    letter = ai_cover_letter.compose(
        resume(), job(), provider, recipient="Hiring Team")
    assert letter.recipient == "Hiring Team"
    assert letter.company == "Globe"
    assert letter.sender.name == "Jane Dev"
    assert letter.letter_date  # a date was set


def test_the_prompt_carries_the_real_bullets_and_matched_skills():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    ai_cover_letter.compose(resume(), job(),
                            Capturing(["Grounded paragraph."]))
    assert "internal tooling" in captured["prompt"]
    assert "PostgreSQL" in captured["prompt"]  # a matched skill to emphasise

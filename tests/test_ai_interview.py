"""
Tests for AI interview answers.

The load-bearing guarantee matches the rest of the AI layer: the answers are
the model's prose, but any answer that claims a skill or a number the resume
does not evidence is dropped in code — that question keeps its deterministic
talking point. A rehearsed answer that quietly invents experience is worse than
none.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_interview
import interview
import resume_model
from explain import ScoreExplanation
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
    base = {"title": "Python Developer", "company": "Globe"}
    return {**base, **overrides}


def prep():
    explanation = ScoreExplanation(
        job_key="jobstreet:id:1", score_percent=70.0,
        title_matches=["Python"], body_matches=["PostgreSQL"],
        missing=["Kubernetes"], demand={"Kubernetes": 9})
    return interview.prepare(resume(), job(), explanation)


class FakeProvider:
    name = "fake"

    def __init__(self, answers):
        self._answers = answers
        self.calls = 0

    def is_available(self):
        return True

    def complete(self, request):
        self.calls += 1
        return LLMResponse(data={"answers": self._answers}, model="fake-1")


class FailingProvider:
    name = "failing"

    def is_available(self):
        return True

    def complete(self, request):
        raise LLMUnavailable("outage")


def _answers_for(sheet, filler="I have relevant Python experience."):
    """One safe answer per question, aligned by count."""
    return [filler for _ in sheet.questions]


# ======================================================
# FALLBACK TO THE DETERMINISTIC SHEET
# ======================================================
def test_no_provider_keeps_the_deterministic_sheet():
    sheet = prep()
    result = ai_interview.enrich(resume(), job(), sheet, NullProvider())
    assert result.ai_used is False
    assert result.base is sheet


def test_a_failing_provider_records_the_reason():
    result = ai_interview.enrich(resume(), job(), prep(), FailingProvider())
    assert result.ai_used is False
    assert "outage" in result.note


def test_a_length_mismatch_drops_all_answers():
    result = ai_interview.enrich(resume(), job(), prep(),
                                 FakeProvider(["only one answer"]))
    assert result.ai_used is False
    assert result.answers == []
    assert result.note


# ======================================================
# GROUNDED ANSWERS ARE KEPT, FABRICATED ONES DROPPED
# ======================================================
def test_grounded_answers_are_kept():
    sheet = prep()
    result = ai_interview.enrich(
        resume(), job(), sheet,
        FakeProvider(_answers_for(sheet)))
    assert result.ai_used is True
    assert result.model == "fake-1"
    assert len(result.answers) == len(sheet.questions)
    assert result.answers[0].prompt == sheet.questions[0].prompt


def test_a_fabricated_answer_is_dropped_but_the_rest_kept():
    sheet = prep()
    answers = _answers_for(sheet)
    # Poison exactly one answer with an invented skill.
    answers[1] = "I rebuilt the platform on Kubernetes and Terraform."
    result = ai_interview.enrich(resume(), job(), sheet, FakeProvider(answers))
    assert result.ai_used is True
    body = " ".join(answer.answer for answer in result.answers)
    assert "Kubernetes" not in body and "Terraform" not in body
    assert len(result.answers) == len(sheet.questions) - 1


def test_all_answers_fabricated_falls_back():
    sheet = prep()
    poison = ["I did it all with Kubernetes." for _ in sheet.questions]
    result = ai_interview.enrich(resume(), job(), sheet, FakeProvider(poison))
    assert result.ai_used is False
    assert result.note


def test_the_company_number_is_not_mistaken_for_an_invented_metric():
    sheet = prep()
    answers = _answers_for(sheet)
    answers[0] = "Your role at 3M is a strong fit for my Python background."
    result = ai_interview.enrich(
        resume(), job(company="3M"), sheet, FakeProvider(answers))
    assert result.ai_used is True


# ======================================================
# THE PROMPT CARRIES THE REAL BULLETS AND QUESTIONS
# ======================================================
def test_the_prompt_carries_the_real_bullets_and_the_questions():
    sheet = prep()
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    ai_interview.enrich(resume(), job(), sheet,
                        Capturing(_answers_for(sheet)))
    assert "internal tooling" in captured["prompt"]
    assert sheet.questions[0].prompt in captured["prompt"]

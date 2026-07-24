"""
Tests for AI resume rewriting.

The load-bearing part is the fabrication verifier: a rewrite that invents a
number or a skill must be rejected in code, because "do not fabricate" cannot
be left to the prompt on a document you send an employer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_rewrite
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

    def __init__(self, rewrites):
        self._rewrites = rewrites
        self.calls = 0

    def is_available(self):
        return True

    def complete(self, request):
        self.calls += 1
        return LLMResponse(data={"rewrites": self._rewrites}, model="fake-1")


class FailingProvider:
    name = "failing"

    def is_available(self):
        return True

    def complete(self, request):
        raise LLMUnavailable("outage")


# ======================================================
# THE FABRICATION VERIFIER (unit)
# ======================================================
RESUME_TEXT = resume().full_text()


def test_a_clean_rewrite_passes():
    assert ai_rewrite.verify_no_fabrication(
        "Automated operations tooling to cut review time by 30%.",
        RESUME_TEXT) is None


def test_an_invented_number_is_caught():
    reason = ai_rewrite.verify_no_fabrication(
        "Cut costs by 80% across the department.", RESUME_TEXT)
    assert reason is not None and "80" in reason


def test_reusing_a_number_already_in_the_resume_is_fine():
    assert ai_rewrite.verify_no_fabrication(
        "Improved throughput, cutting effort 30%.", RESUME_TEXT) is None


def test_an_invented_skill_is_caught():
    reason = ai_rewrite.verify_no_fabrication(
        "Built the tooling in Django on Kubernetes.", RESUME_TEXT)
    assert reason is not None
    assert "Kubernetes" in reason or "Django" in reason


def test_a_skill_the_resume_has_is_allowed():
    assert ai_rewrite.verify_no_fabrication(
        "Wrote Python automation for the operations team.", RESUME_TEXT) is None


# ======================================================
# END TO END
# ======================================================
def test_no_provider_returns_the_resume_unchanged():
    original = resume()
    result = ai_rewrite.rewrite_for_job(original, job(), NullProvider())
    assert result.ai_used is False
    assert result.resume.all_bullets() == original.all_bullets()


def test_a_failing_provider_leaves_the_resume_intact():
    result = ai_rewrite.rewrite_for_job(resume(), job(), FailingProvider())
    assert result.ai_used is False


def test_good_rewrites_are_applied():
    provider = FakeProvider([
        "Delivered internal tooling relied on daily by operations.",
        "Automated review workflows in Python, cutting effort 30%.",
    ])
    result = ai_rewrite.rewrite_for_job(resume(), job(), provider)
    assert result.ai_used is True
    assert result.rewritten == 2
    assert "Delivered internal tooling" in result.resume.all_bullets()[0]


def test_a_fabricated_rewrite_is_dropped_and_the_original_kept():
    provider = FakeProvider([
        "Built internal tooling used by the operations team.",   # unchanged
        "Cut manual review by 95% using Docker and Kubernetes.",  # fabricated
    ])
    result = ai_rewrite.rewrite_for_job(resume(), job(), provider)
    bullets = result.resume.all_bullets()
    assert "95%" not in " ".join(bullets)
    assert "Docker" not in " ".join(bullets)
    # the genuine original is retained verbatim
    assert "cut manual review time by 30%" in " ".join(bullets).lower()
    assert len(result.rejections) == 1


def test_the_original_resume_object_is_never_mutated():
    original = resume()
    before = list(original.all_bullets())
    ai_rewrite.rewrite_for_job(original, job(), FakeProvider(
        ["New A.", "New B."]))
    assert original.all_bullets() == before, "the input must be left untouched"


def test_a_length_mismatch_rejects_the_whole_batch():
    """A misaligned reply could put the wrong rewrite on the wrong bullet."""
    provider = FakeProvider(["only one rewrite for two bullets"])
    result = ai_rewrite.rewrite_for_job(resume(), job(), provider)
    assert result.ai_used is False
    assert result.rewritten == 0


def test_an_unchanged_rewrite_counts_as_kept():
    provider = FakeProvider([
        "Built internal tooling used by the operations team.",
        "Wrote Python scripts that cut manual review time by 30%.",
    ])
    result = ai_rewrite.rewrite_for_job(resume(), job(), provider)
    assert result.rewritten == 0
    assert result.kept_original == 2


def test_the_bullets_sent_are_the_resume_bullets():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    ai_rewrite.rewrite_for_job(resume(), job(),
                               Capturing(["a.", "b."]))
    assert "internal tooling" in captured["prompt"]
    assert "PostgreSQL" in captured["prompt"]   # a target skill from the job

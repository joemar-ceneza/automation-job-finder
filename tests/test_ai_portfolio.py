"""
Tests for AI portfolio pitches.

The guarantee: a pitch may only say what the project itself states. Crediting a
project with a technology it never used is a lie you'd repeat in an interview,
so it is caught in code and that project keeps its deterministic match.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_portfolio
import portfolio
from llm import LLMResponse, LLMUnavailable, NullProvider

PROJECTS = [
    portfolio.Project(
        name="Job Finder", url="https://example.com/jf",
        tech=["Python", "Playwright", "PostgreSQL"],
        summary="Scrapes and scores job ads.",
        highlights=["Cut manual review time by 30%"]),
    portfolio.Project(
        name="Shop Front", url="https://example.com/shop",
        tech=["React.js", "Node.js"], summary="An online store."),
]


def job(**overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Developer",
            "company": "Acme", "description": "Playwright and PostgreSQL."}
    return {**base, **overrides}


def base_match(j=None):
    return portfolio.match_job(j or job(), PROJECTS)


def pitches(*items):
    return {"pitches": [{"why_it_fits": why, "lead_with": lead}
                        for why, lead in items]}


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


CLEAN = pitches(
    ("It uses Playwright and Python to collect job ads, which this role needs.",
     "The Playwright automation."),
    ("A React.js store showing frontend work.", "The React.js UI."),
)


# ======================================================
# FALLBACK
# ======================================================
def test_no_provider_keeps_the_ranking():
    result = ai_portfolio.enrich(job(), base_match(), NullProvider())
    assert result.ai_used is False
    assert result.base.matches


def test_no_projects_short_circuits():
    empty = portfolio.match_job(job(), [])
    result = ai_portfolio.enrich(job(), empty, FakeProvider(CLEAN))
    assert result.ai_used is False
    assert result.note


def test_a_failing_provider_records_the_reason():
    result = ai_portfolio.enrich(job(), base_match(), FailingProvider())
    assert result.ai_used is False
    assert "outage" in result.note


def test_a_length_mismatch_drops_all_pitches():
    result = ai_portfolio.enrich(job(), base_match(),
                                 FakeProvider(pitches(("only one", "pitch"))))
    assert result.ai_used is False
    assert result.pitches == []


# ======================================================
# THE FABRICATION GUARANTEE
# ======================================================
def test_a_pitch_crediting_unused_tech_is_dropped():
    poisoned = pitches(
        ("Deployed on Kubernetes with Docker orchestration.", "The cluster."),
        ("A React.js store showing frontend work.", "The React.js UI."),
    )
    result = ai_portfolio.enrich(job(), base_match(), FakeProvider(poisoned))
    assert result.ai_used is True
    names = [pitch.match.project.name for pitch in result.pitches]
    assert "Job Finder" not in names          # the fabricated one is gone
    assert "Shop Front" in names              # the honest one survives


def test_a_pitch_inventing_a_metric_is_dropped():
    poisoned = pitches(
        ("It cut costs by 90% across the business.", "The savings."),
        ("A React.js store showing frontend work.", "The React.js UI."),
    )
    result = ai_portfolio.enrich(job(), base_match(), FakeProvider(poisoned))
    names = [pitch.match.project.name for pitch in result.pitches]
    assert "Job Finder" not in names


def test_a_metric_the_project_states_is_allowed():
    honest = pitches(
        ("It cut manual review time by 30% using Playwright.", "The pipeline."),
        ("A React.js store showing frontend work.", "The React.js UI."),
    )
    result = ai_portfolio.enrich(job(), base_match(), FakeProvider(honest))
    names = [pitch.match.project.name for pitch in result.pitches]
    assert "Job Finder" in names


def test_the_check_is_deliberately_conservative_on_word_forms():
    """
    A project summary saying "Scrapes job ads" does not, to the alias map,
    evidence the skill "Web Scraping" — so a pitch using that phrasing is
    dropped. Erring this way is the point: the cost is a missing pitch (the
    ranking still stands), while the opposite error is a claim you'd have to
    defend in an interview.
    """
    borderline = pitches(
        ("Built a web scraping pipeline for job ads.", "The scraping."),
        ("A React.js store showing frontend work.", "The React.js UI."),
    )
    result = ai_portfolio.enrich(job(), base_match(), FakeProvider(borderline))
    names = [pitch.match.project.name for pitch in result.pitches]
    assert "Job Finder" not in names
    assert result.base.matches, "the deterministic ranking is untouched"


def test_all_pitches_fabricated_falls_back():
    poisoned = pitches(("Built on Kubernetes.", "The cluster."),
                       ("Built on Kubernetes.", "The cluster."))
    result = ai_portfolio.enrich(job(), base_match(), FakeProvider(poisoned))
    assert result.ai_used is False
    assert result.note


# ======================================================
# ENRICHMENT
# ======================================================
def test_clean_pitches_are_attached_in_ranking_order():
    result = ai_portfolio.enrich(job(), base_match(), FakeProvider(CLEAN))
    assert result.ai_used is True
    assert result.pitches[0].match.project.name == "Job Finder"
    assert "Playwright" in result.pitches[0].why_it_fits
    assert result.model == "fake-1"


def test_the_prompt_carries_the_projects_and_what_the_job_wants():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    ai_portfolio.enrich(job(), base_match(), Capturing(CLEAN))
    assert "Job Finder" in captured["prompt"]
    assert "job_wants_these_of_its_tech" in captured["prompt"]

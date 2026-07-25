"""
Tests for the AI learning roadmap.

The rule enforced in code: the model must not supply links. Curated resources
are the only ones the user sees, because a model asked for course URLs produces
plausible ones that 404. Everything else follows the AI layer's usual contract —
enrich the computed plan, never replace it, degrade cleanly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_learning
import learning
from llm import LLMResponse, LLMUnavailable, NullProvider

RESUME = "Python developer. Skills: Python, PostgreSQL, Git, Linux."
SKILLS = ["Python", "PostgreSQL", "Git", "Linux"]

GOOD = {
    "roadmap": "Start with Docker, then Kubernetes once containers click.",
    "weekly_plan": ["Week 1: Docker images and containers.",
                    "Week 2: Compose and volumes."],
    "projects": ["Containerise your job scraper with Docker."],
}


def base_plan():
    return learning.plan(RESUME, [{"skill": "Kubernetes", "demand": 30}],
                         limit=5)


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


# ======================================================
# FALLBACK
# ======================================================
def test_no_provider_keeps_the_computed_plan():
    result = ai_learning.enrich(base_plan(), SKILLS, NullProvider())
    assert result.ai_used is False
    assert result.base.steps


def test_a_failing_provider_records_the_reason():
    result = ai_learning.enrich(base_plan(), SKILLS, FailingProvider())
    assert result.ai_used is False
    assert "outage" in result.note


def test_an_empty_plan_short_circuits():
    empty = learning.plan(RESUME, [], limit=5)
    result = ai_learning.enrich(empty, SKILLS, FakeProvider(GOOD))
    assert result.ai_used is False
    assert result.note


def test_an_empty_roadmap_falls_back():
    result = ai_learning.enrich(base_plan(), SKILLS,
                                FakeProvider(dict(GOOD, roadmap="   ")))
    assert result.ai_used is False


# ======================================================
# THE NO-INVENTED-LINKS RULE
# ======================================================
def test_a_roadmap_containing_a_link_is_refused():
    poisoned = dict(GOOD,
                    roadmap="Follow https://totally-real-course.example/docker")
    result = ai_learning.enrich(base_plan(), SKILLS, FakeProvider(poisoned))
    assert result.ai_used is False
    assert "link" in result.note.lower()


def test_weekly_items_with_links_are_dropped_but_the_rest_kept():
    poisoned = dict(GOOD, weekly_plan=[
        "Week 1: Docker basics.",
        "Week 2: see https://made-up-course.example/k8s",
    ])
    result = ai_learning.enrich(base_plan(), SKILLS, FakeProvider(poisoned))
    assert result.ai_used is True
    assert result.weekly_plan == ["Week 1: Docker basics."]


def test_project_items_with_links_are_dropped():
    poisoned = dict(GOOD, projects=["Build a thing (www.fake.example/guide)",
                                    "Containerise your scraper."])
    result = ai_learning.enrich(base_plan(), SKILLS, FakeProvider(poisoned))
    assert result.projects == ["Containerise your scraper."]


# ======================================================
# ENRICHMENT
# ======================================================
def test_a_clean_roadmap_is_attached():
    result = ai_learning.enrich(base_plan(), SKILLS, FakeProvider(GOOD))
    assert result.ai_used is True
    assert "Docker" in result.roadmap
    assert len(result.weekly_plan) == 2
    assert result.model == "fake-1"


def test_the_prompt_carries_the_computed_order_and_hours():
    captured = {}

    class Capturing(FakeProvider):
        def complete(self, request):
            captured["prompt"] = request.prompt
            return super().complete(request)

    ai_learning.enrich(base_plan(), SKILLS, Capturing(GOOD))
    assert "Docker" in captured["prompt"]
    assert "total_hours" in captured["prompt"]
    assert "already_known" in captured["prompt"]

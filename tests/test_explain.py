"""
Tests for the deterministic score explanation.

The regression pinned here: "missing" was computed by comparing skill names,
but the extractor speaks MASTER_SKILLS ("React JS", "REST API") while
skills.txt uses the candidate's own wording ("React.js", "REST API
development"). Skills plainly present in the resume were reported missing,
which is worse than saying nothing — it sends you off to learn what you have.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import explain

RESUME_TEXT = (
    "Full stack developer. Skills: Python, React.js, Next.js, Node.js, "
    "REST API development, API integration, Process automation, "
    "Data extraction, PostgreSQL, Playwright, Git."
)
RESUME_SKILLS = ["Python", "React.js", "REST API development",
                 "Process automation", "PostgreSQL", "Playwright"]


def job(title: str, description: str) -> dict:
    return {"job_key": "jobstreet:id:1", "title": title,
            "description": description, "score_percent": 70.0,
            "matched_skills": "Python (title), PostgreSQL"}


# ======================================================
# THE VOCABULARY MISMATCH
# ======================================================
def test_differently_worded_skills_are_not_reported_missing():
    result = explain.explain_job(
        job("Python Developer", "We use React and REST APIs daily."),
        RESUME_SKILLS, RESUME_TEXT)
    assert "React JS" not in result.missing
    assert "REST API" not in result.missing


def test_genuinely_absent_skills_are_still_reported():
    result = explain.explain_job(
        job("Python Developer", "Kubernetes and Terraform required."),
        RESUME_SKILLS, RESUME_TEXT)
    assert "Kubernetes" in result.missing
    assert "Terraform" in result.missing


def test_aliases_count_as_present():
    """'NodeJS' in an advert must match 'Node.js' in the resume."""
    result = explain.explain_job(
        job("Backend Developer", "Strong NodeJS experience."),
        RESUME_SKILLS, RESUME_TEXT)
    assert "Node JS" not in result.missing


# ======================================================
# THE NARRATIVE
# ======================================================
def test_title_and_body_matches_are_separated():
    result = explain.explain_job(
        job("Python Developer", "PostgreSQL too."), RESUME_SKILLS, RESUME_TEXT)
    assert result.title_matches == ["Python"]
    assert result.body_matches == ["PostgreSQL"]


def test_points_are_reported():
    result = explain.explain_job(
        job("Python Developer", "PostgreSQL too."), RESUME_SKILLS, RESUME_TEXT)
    assert result.points_earned > 0
    assert result.points_possible > 0
    assert any("points" in line for line in result.lines)


def test_a_job_matching_nothing_says_so():
    unmatched = {"job_key": "jobstreet:id:2", "title": "Pastry Chef",
                 "description": "Baking.", "score_percent": 0.0,
                 "matched_skills": ""}
    result = explain.explain_job(unmatched, RESUME_SKILLS, RESUME_TEXT)
    assert any("None of your skills" in line for line in result.lines)


def test_explanation_is_produced_without_resume_text():
    """Falls back to the skill names rather than failing."""
    result = explain.explain_job(
        job("Python Developer", "PostgreSQL too."), RESUME_SKILLS)
    assert result.lines

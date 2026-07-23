"""
Tests for the Standard-mode resume optimiser.

The defining constraint: it restructures and reports, it never rewrites. Any
change to a bullet's wording would be a bug, because that is AI mode's job and
it needs a fabrication verifier first.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import optimizer
import resume_model

RESUME = """# Jane Dev
Software Engineer
jane@example.com · +63 900 000 0000 · Manila

## Summary

Backend engineer who automates things.

## Skills

Python, PostgreSQL, Git

## Experience

### Support Engineer — Helpdesk Co
Manila · 2019 - 2021
- Answered tickets and escalated issues.
- Wrote Python scripts to speed up triage by 40%.

### Backend Developer — Acme Corp
Manila · 2021 - Present
- Maintained internal tooling.
- Built REST APIs in Django backed by PostgreSQL.

## Education

### BS Computer Science — State University
2015 - 2019
"""


@pytest.fixture
def resume():
    return resume_model.parse_markdown(RESUME)


def job(title: str, description: str) -> dict:
    return {"job_key": "jobstreet:id:1", "title": title,
            "description": description, "company": "Acme"}


# ======================================================
# THE CORE PROMISE — NO REWRITING
# ======================================================
def test_wording_is_never_changed(resume):
    original = set(resume.all_bullets())
    result = optimizer.optimise(resume, job(
        "Python Developer", "Django, PostgreSQL and REST API work."))
    assert set(result.resume.all_bullets()) == original


def test_no_section_or_entry_is_lost(resume):
    result = optimizer.optimise(resume, job("Python Developer", "Django."))
    assert {s.name for s in result.resume.sections} == \
           {s.name for s in resume.sections}
    assert len(result.resume.all_bullets()) == len(resume.all_bullets())


# ======================================================
# RESTRUCTURING
# ======================================================
def test_relevant_bullets_are_promoted_within_an_entry(resume):
    result = optimizer.optimise(resume, job(
        "Python Developer", "We need Django and PostgreSQL experience."))
    acme = next(entry for section in result.resume.sections
                for entry in section.entries
                if entry.organisation == "Acme Corp")
    assert "Django" in acme.bullets[0], acme.bullets
    assert result.promoted_bullets > 0


def test_promotion_is_stable_for_equally_relevant_bullets(resume):
    """Bullets the job says nothing about must keep their original order."""
    result = optimizer.optimise(resume, job("Chef", "Cooking only."))
    assert result.resume.all_bullets() == resume.all_bullets()


def test_sections_evidencing_the_job_come_first(resume):
    result = optimizer.optimise(resume, job(
        "Python Developer", "Django, PostgreSQL, REST API."))
    assert result.section_order[0] in {"Skills", "Experience"}


# ======================================================
# ADVICE
# ======================================================
def test_skills_present_in_experience_but_unlisted_are_surfaced(resume):
    """Django is in a bullet but not the Skills section — free marks."""
    result = optimizer.optimise(resume, job("Dev", "Django and PostgreSQL."))
    assert "Django" in result.unmentioned_skills
    assert "PostgreSQL" not in result.unmentioned_skills  # already listed


def test_skills_absent_everywhere_are_reported_missing(resume):
    result = optimizer.optimise(resume, job("Dev", "We use Kubernetes daily."))
    assert "Kubernetes" in result.missing_skills
    assert any("Kubernetes" in change for change in result.changes)


def test_a_well_matched_resume_says_so(resume):
    result = optimizer.optimise(resume, job("Chef", "Cooking."))
    assert result.changes


# ======================================================
# ATS RUBRIC
# ======================================================
def test_score_is_within_bounds(resume):
    result = optimizer.optimise(resume, job("Dev", "Python and Django."))
    assert 0 <= result.ats_score <= 100
    assert sum(check.max_points for check in result.checks) == 100


def test_missing_contact_details_cost_points():
    text = RESUME.replace("jane@example.com · +63 900 000 0000 · Manila", "")
    stripped = resume_model.parse_markdown(text)
    check = optimizer._check_contact(stripped)
    assert check.points < check.max_points
    assert "email" in check.detail


def test_quantified_bullets_are_rewarded(resume):
    check = optimizer._check_quantified(resume)
    assert check.points > 0, "the 40% bullet should count"


def test_a_resume_with_no_numbers_scores_zero_there():
    plain = resume_model.parse_markdown(
        "# N\n\n## Experience\n\n### Dev — Acme\n2020 - 2021\n- Did work.\n")
    assert optimizer._check_quantified(plain).points == 0


def test_missing_headings_cost_points():
    sparse = resume_model.parse_markdown(
        "# N\n\n## Hobbies\n\n### Chess\n- Played chess.\n")
    check = optimizer._check_headings(sparse)
    assert check.points == 0


def test_entries_without_dates_cost_points():
    undated = resume_model.parse_markdown(
        "# N\n\n## Experience\n\n### Dev — Acme\n- Did work.\n")
    check = optimizer._check_dates(undated)
    assert check.points < check.max_points


def test_overlong_bullets_cost_points():
    wordy = "word " * 60
    verbose = resume_model.parse_markdown(
        f"# N\n\n## Experience\n\n### Dev — Acme\n2020 - 2021\n- {wordy}\n")
    check = optimizer._check_bullet_length(verbose)
    assert check.points < check.max_points


def test_job_with_no_recognised_skills_does_not_crash(resume):
    result = optimizer.optimise(resume, job("Chef", "Make food."))
    assert result.ats_score > 0
    assert result.missing_skills == []

"""
Tests for the weighted scoring scale and its calibration helper.

The bug these guard against: scores used to be normalised against every skill
appearing in the job title, an unreachable ceiling that squashed a real corpus
of 315 jobs into 0-13% with hundreds of ties.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import matcher

SKILLS = ["Python", "SQL", "Playwright", "React.js", "MongoDB", "Node.js",
          "Git", "Docker", "AWS", "TypeScript", "PostgreSQL", "Express.js"]


@pytest.fixture(autouse=True)
def fixed_target(monkeypatch):
    """Pin the scale so tests do not drift when config is retuned."""
    monkeypatch.setattr(config, "TARGET_MATCH_SKILLS", 8)


def score(title: str, body: str, skills: list[str] = None) -> float:
    return matcher._score_job(title, body, skills or SKILLS)[0]


# ======================================================
# SCALE
# ======================================================
def test_strong_match_escapes_the_old_compressed_band():
    """
    The regression: under the old formula the best of 315 real jobs scored
    13.2%. A job hitting two thirds of the skill list must now land in a band
    a human can read. The exact constant depends on TARGET_MATCH_SKILLS, which
    is calibrated per corpus via --calibrate, so this asserts the property
    (well clear of the old ceiling) rather than a specific number.
    """
    result = score("Senior Python Developer (React.js, Node.js)",
                   "Work with SQL, MongoDB, Git, Playwright and TypeScript.")
    assert result > 40, f"still compressed near the old ceiling, got {result}"


def test_irrelevant_job_scores_zero():
    assert score("Warehouse Assistant", "Lifting boxes all day.") == 0.0


def test_partial_match_lands_between():
    weak = score("Warehouse Assistant", "Lifting boxes.")
    mid = score("Python Developer", "Some SQL exposure helpful.")
    strong = score("Senior Python Developer (React.js, Node.js)",
                   "SQL, MongoDB, Git, Playwright, TypeScript.")
    assert weak < mid < strong


def test_score_is_clamped_at_100():
    everything = " ".join(SKILLS)
    assert score(everything, everything) == 100.0


def test_extra_skills_beyond_the_target_do_not_dilute():
    """
    The old formula divided by the whole skill list, so adding a skill a job
    never mentions lowered its score. Above TARGET_MATCH_SKILLS that must stop.
    """
    base = SKILLS[:8]                                   # exactly the target
    padded = base + [f"Filler{i}" for i in range(40)]
    assert score("Python Developer", "SQL and Git.", base) == \
           score("Python Developer", "SQL and Git.", padded)


def test_short_skill_list_can_still_reach_the_top():
    """
    Below the target the denominator shrinks with the list, so a candidate
    with three skills matched on all three is a perfect fit, not a 12% one.
    """
    assert score("Python SQL Git Developer", "", ["Python", "SQL", "Git"]) == 100.0


def test_title_hits_outweigh_body_hits():
    in_title = score("Python Developer", "Nothing else relevant.")
    in_body = score("Software Engineer", "Some Python involved.")
    assert in_title > in_body


def test_empty_skill_list_is_safe():
    assert matcher._score_job("Python Developer", "SQL", []) == (0.0, [], [])


# ======================================================
# CALIBRATION
# ======================================================
def test_calibrate_refuses_thin_data():
    result = matcher.suggest_target_match([{"matched_skills": "Python"}] * 5)
    assert result["suggested"] is None
    assert result["table"] == []


def test_calibrate_returns_a_table_with_enough_data():
    rows = [{"matched_skills": "Python (title), SQL, Git"}] * 200
    result = matcher.suggest_target_match(rows)
    assert result["sample"] == 200
    assert len(result["table"]) > 0
    assert all(len(entry) == 4 for entry in result["table"])


def test_weighted_reconstruction_counts_title_hits_higher():
    title_only = matcher._weighted_from_matched("Python (title)")
    body_only = matcher._weighted_from_matched("Python")
    assert title_only == config.TITLE_MATCH_WEIGHT
    assert body_only == config.BODY_MATCH_WEIGHT
    assert matcher._weighted_from_matched("") == 0

"""
Tests for scale calibration.

The scale is pinned to the top of the distribution, not the middle: in a job
search most adverts genuinely are not a match, so a low median is correct and
forcing it to 50 would flatter bad jobs.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import matcher


def rows(count: int, matched: str, description: str = "") -> list[dict]:
    return [{"matched_skills": matched, "description": description}] * count


# ======================================================
# SAMPLE-SIZE GATE
# ======================================================
def test_thin_data_yields_no_suggestion():
    result = matcher.suggest_target_match(rows(5, "Python (title)"))
    assert result["suggested"] is None
    assert result["table"] == []


def test_enough_data_yields_a_table():
    result = matcher.suggest_target_match(
        rows(config.CALIBRATION_MIN_JOBS, "Python (title), SQL, Git"))
    assert result["sample"] == config.CALIBRATION_MIN_JOBS
    assert len(result["table"]) > 0


# ======================================================
# WHAT THE SUGGESTION OPTIMISES FOR
# ======================================================
def test_the_suggestion_puts_the_best_job_in_the_top_band():
    sample = (rows(180, "Python") +
              rows(20, "Python (title), SQL (title), Git, Docker"))
    result = matcher.suggest_target_match(sample)
    assert result["suggested"] is not None

    target = result["suggested"]
    top = max(entry[3] for entry in result["table"] if entry[0] == target)
    assert 80 <= top <= 95, f"best job scored {top} at K={target}"


def test_a_low_median_is_not_treated_as_a_problem():
    """Most search results genuinely are not a match — that is the truth."""
    strong = "Python (title), SQL (title), Git (title), Docker, AWS, Linux"
    sample = rows(190, "Python") + rows(10, strong)
    result = matcher.suggest_target_match(sample)

    assert result["suggested"] is not None, \
        "a low median must not block a suggestion"
    median = next(entry[1] for entry in result["table"]
                  if entry[0] == result["suggested"])
    assert median < 50, "the median should stay low, not be tuned upward"


# ======================================================
# REPORTING THE CORPUS SHAPE
# ======================================================
def test_full_description_share_is_reported():
    """
    The right scale depends on how much text was scored, so --calibrate says
    what share of the corpus carried a full description.
    """
    long_text = "x" * (config.FULL_DESCRIPTION_CHARS + 100)
    sample = (rows(100, "Python", description="short teaser")
              + rows(100, "Python", description=long_text))
    result = matcher.suggest_target_match(sample)
    assert result["with_full_text"] == 100


def test_teaser_only_corpus_reports_zero_full_text():
    result = matcher.suggest_target_match(
        rows(200, "Python", description="short"))
    assert result["with_full_text"] == 0


def test_median_match_count_is_reported():
    result = matcher.suggest_target_match(rows(200, "Python (title), SQL"))
    assert result["median_matches"] == 2


# ======================================================
# WEIGHT RECONSTRUCTION
# ======================================================
@pytest.mark.parametrize("matched, expected", [
    ("", 0),
    ("Python", config.BODY_MATCH_WEIGHT),
    ("Python (title)", config.TITLE_MATCH_WEIGHT),
    ("Python (title), SQL", config.TITLE_MATCH_WEIGHT + config.BODY_MATCH_WEIGHT),
])
def test_weight_is_rebuilt_from_stored_matches(matched, expected):
    assert matcher._weighted_from_matched(matched) == expected

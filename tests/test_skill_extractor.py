"""Tests for dictionary-based skill extraction and categorisation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skill_extractor


def names(extracted) -> set[str]:
    return {skill for skill, _category, _in_title in extracted}


# ======================================================
# EXTRACTION
# ======================================================
def test_finds_skills_in_title_and_body():
    found = skill_extractor.extract_skills(
        "Senior Python Developer",
        "You will build REST APIs with Django and PostgreSQL, deployed on AWS.")
    assert {"Python", "Django", "PostgreSQL", "AWS"} <= names(found)


def test_marks_title_hits():
    found = skill_extractor.extract_skills("Python Developer",
                                           "Some Docker experience helps.")
    by_skill = {skill: in_title for skill, _cat, in_title in found}
    assert by_skill["Python"] is True
    assert by_skill["Docker"] is False


def test_reports_nothing_for_an_unrelated_advert():
    found = skill_extractor.extract_skills(
        "Warehouse Assistant", "Lifting boxes and managing stock rotation.")
    assert names(found) == set()


def test_handles_empty_input():
    assert skill_extractor.extract_skills("", "") == []
    assert skill_extractor.extract_skills(None, None) == []


# ======================================================
# THE AMBIGUOUS-WORD GUARD
# ======================================================
def test_go_the_language_is_not_go_the_verb():
    """'go-getter' must not register the Go programming language."""
    found = skill_extractor.extract_skills(
        "Sales Associate",
        "We want a go-getter who will go the extra mile for customers.")
    assert "Go" not in names(found)


def test_go_counts_with_hiring_context():
    found = skill_extractor.extract_skills(
        "Backend Engineer",
        "Strong experience with Go and distributed systems.")
    assert "Go" in names(found)


def test_ambiguous_word_in_title_always_counts():
    found = skill_extractor.extract_skills("Go Developer", "Backend work.")
    assert "Go" in names(found)


# ======================================================
# CATEGORIES
# ======================================================
def test_categories_route_to_the_right_bucket():
    assert skill_extractor.category_for("Python") == "language"
    assert skill_extractor.category_for("Django") == "framework"
    assert skill_extractor.category_for("PostgreSQL") == "database"
    assert skill_extractor.category_for("AWS") == "cloud"
    assert skill_extractor.category_for("TensorFlow") == "ai"


def test_unknown_skill_falls_back_rather_than_disappearing():
    assert skill_extractor.category_for("Some New Thing") == "tool"


# ======================================================
# BATCH SHAPE
# ======================================================
def test_extract_for_rows_returns_insertable_tuples():
    rows = [{
        "job_key": "jobstreet:id:1",
        "title": "Python Developer",
        "teaser": "Django and PostgreSQL.",
        "description": "",
    }]
    extracted = skill_extractor.extract_for_rows(rows)
    assert all(len(entry) == 4 for entry in extracted)
    assert all(entry[0] == "jobstreet:id:1" for entry in extracted)
    assert all(entry[3] in (0, 1) for entry in extracted)
    assert {entry[1] for entry in extracted} >= {"Python", "Django",
                                                 "PostgreSQL"}

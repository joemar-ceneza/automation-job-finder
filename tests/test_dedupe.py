"""
Tests for repeat-posting detection.

The constraint these encode: the scraped data only supports same-employer
matching. OnlineJobs.ph publishes no employer name, and title alone collides
constantly — "Full Stack Developer" appeared at twelve different JobStreet
employers in one 232-job sample.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dedupe


def job(key: str, title: str, company: str = "", first_seen: str = "2026-07-01",
        source: str = "jobstreet") -> dict:
    return {"job_key": key, "title": title, "company": company,
            "first_seen": first_seen, "source": source}


# ======================================================
# WHAT COUNTS AS A DUPLICATE
# ======================================================
def test_same_employer_same_role_is_a_duplicate():
    found = dedupe.find_duplicates([
        job("a", "Python Developer", "Acme Corp", "2026-07-01"),
        job("b", "Python Developer", "Acme Corp", "2026-07-05"),
    ])
    assert found == {"b": "a"}, "the later posting defers to the earlier one"


def test_seniority_and_posting_noise_are_ignored():
    found = dedupe.find_duplicates([
        job("a", "Senior Python Developer (Remote)", "Acme Corp", "2026-07-01"),
        job("b", "Python Developer - Full Time", "Acme Corp", "2026-07-05"),
    ])
    assert found == {"b": "a"}


def test_company_suffixes_do_not_prevent_a_match():
    found = dedupe.find_duplicates([
        job("a", "Backend Developer", "Acme Inc.", "2026-07-01"),
        job("b", "Backend Developer", "Acme Incorporated", "2026-07-02"),
    ])
    assert found == {"b": "a"}


def test_earliest_posting_is_the_canonical_one():
    found = dedupe.find_duplicates([
        job("late", "QA Engineer", "Acme", "2026-07-09"),
        job("early", "QA Engineer", "Acme", "2026-07-02"),
        job("mid", "QA Engineer", "Acme", "2026-07-05"),
    ])
    assert set(found) == {"late", "mid"}
    assert set(found.values()) == {"early"}


# ======================================================
# WHAT MUST NOT COUNT
# ======================================================
def test_same_title_at_different_employers_is_not_a_duplicate():
    """The single most important guard — this title is genuinely everywhere."""
    found = dedupe.find_duplicates([
        job("a", "Full Stack Developer", "KMC Solutions"),
        job("b", "Full Stack Developer", "Globe Telecom"),
        job("c", "Full Stack Developer", "Get Devs"),
    ])
    assert found == {}


def test_placeholder_employers_never_match():
    """'Private Advertiser' is many unrelated firms, not one."""
    found = dedupe.find_duplicates([
        job("a", "AI Automation Specialist", "Private Advertiser"),
        job("b", "AI Automation Specialist", "Private Advertiser"),
        job("c", "AI Automation Specialist", "Confidential"),
    ])
    assert found == {}


def test_listings_without_an_employer_are_skipped():
    """Every OnlineJobs.ph listing lands here — nothing to anchor on."""
    found = dedupe.find_duplicates([
        job("a", "Backend Developer", "", source="onlinejobs"),
        job("b", "Backend Developer", "", source="onlinejobs"),
    ])
    assert found == {}


def test_cross_site_pairs_are_not_claimed():
    """
    A real cross-site duplicate cannot be confirmed, because the OnlineJobs
    side carries no employer. Staying silent beats guessing.
    """
    found = dedupe.find_duplicates([
        job("js", "Python Automation Developer", "Accion Labs",
            source="jobstreet"),
        job("oj", "Python Automation Developer", "", source="onlinejobs"),
    ])
    assert found == {}


def test_different_roles_at_one_employer_are_kept_apart():
    found = dedupe.find_duplicates([
        job("a", "Backend Developer", "Acme"),
        job("b", "Frontend Developer", "Acme"),
    ])
    assert found == {}


def test_empty_input_is_safe():
    assert dedupe.find_duplicates([]) == {}

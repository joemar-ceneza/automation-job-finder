"""
Tests for storing fuller job descriptions on a re-scrape.

The regression: --full-desc fetched a full description for a job already in
the database and then discarded it, because only newly-seen jobs were written.
Running --full-desc over an existing corpus therefore changed nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway database, so the real corpus is never touched."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    import db_handler
    db_handler.init_db()
    return db_handler


def job_row(job_key: str, description: str) -> dict:
    return {
        "job_key": job_key, "title": "Python Developer", "company": "Acme",
        "location": "Manila", "url": "https://example.com", "source": "jobstreet",
        "salary": "", "salary_min": "", "salary_max": "",
        "work_arrangement": "", "listing_date": "", "status": "saved",
        "search_keyword": "python", "score_percent": 10.0,
        "matched_skills": "Python", "required_years": "",
        "description": description,
    }


def test_a_longer_description_replaces_the_teaser(db):
    db.insert_jobs([job_row("jobstreet:id:1", "Short teaser.")])
    updated = db.update_descriptions(
        [{"job_key": "jobstreet:id:1",
          "description": "A much longer full description " * 10}])
    assert updated == ["jobstreet:id:1"]
    assert len(db.fetch_jobs(["jobstreet:id:1"])[0]["description"]) > 100


def test_a_teaser_never_overwrites_a_full_description(db):
    """A fast run after a --full-desc run must not lose the fuller text."""
    full = "A much longer full description " * 10
    db.insert_jobs([job_row("jobstreet:id:1", full)])
    assert db.update_descriptions(
        [{"job_key": "jobstreet:id:1", "description": "Short teaser."}]) == []
    assert db.fetch_jobs(["jobstreet:id:1"])[0]["description"] == full


def test_unknown_jobs_are_ignored(db):
    assert db.update_descriptions(
        [{"job_key": "jobstreet:id:missing", "description": "Anything."}]) == []


def test_blank_descriptions_are_ignored(db):
    db.insert_jobs([job_row("jobstreet:id:1", "Short teaser.")])
    assert db.update_descriptions(
        [{"job_key": "jobstreet:id:1", "description": "   "}]) == []
    assert db.fetch_jobs(["jobstreet:id:1"])[0]["description"] == "Short teaser."


def test_empty_input_is_safe(db):
    assert db.update_descriptions([]) == []


def test_a_fuller_description_changes_the_score(db):
    """The point of the fix: more text means more matched skills."""
    import matcher
    skills = ["Python", "Django", "PostgreSQL", "Playwright"]

    db.insert_jobs([job_row("jobstreet:id:1", "We need a developer.")])
    before = matcher.rank_jobs(db.fetch_jobs(["jobstreet:id:1"]), skills)

    db.update_descriptions([{
        "job_key": "jobstreet:id:1",
        "description": "We need a developer experienced with Django, "
                       "PostgreSQL and Playwright automation.",
    }])
    after = matcher.rank_jobs(db.fetch_jobs(["jobstreet:id:1"]), skills)

    assert after[0]["score_percent"] > before[0]["score_percent"]

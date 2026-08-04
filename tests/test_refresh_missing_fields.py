"""
Tests for healing already-seen jobs.

A job is scored once and thereafter only has last_seen touched, which means a
scraping bug stays baked into the corpus even after the code is fixed — the
affected rows are "already seen", so nothing ever revisits them. That is exactly
how 643 JobStreet rows kept a blank location after the selector was corrected.

The rule that makes this safe is that it only ever fills blanks. Overwriting
would let a fast teaser-only run clobber richer data collected by an earlier
--full-desc run.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import db_handler


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "jobs.db"))
    db_handler.init_db()
    return db_handler


def store(db, **overrides):
    row = {"job_key": "jobstreet:id:1", "title": "Python Developer",
           "company": "Acme", "location": "", "url": "https://x/1",
           "source": "jobstreet", "salary": "", "score_percent": 50.0,
           "matched_skills": "python", "required_years": None,
           "description": "d", "search_keyword": "developer"}
    row.update(overrides)
    db.insert_jobs([row])
    return row


def stored(db, job_key="jobstreet:id:1"):
    return db.fetch_jobs([job_key])[0]


# ======================================================
# FILLING BLANKS
# ======================================================
def test_a_blank_location_is_filled(db):
    store(db, location="")
    healed = db.refresh_missing_fields(
        [{"job_key": "jobstreet:id:1", "location": "Makati City"}])
    assert healed == 1
    assert stored(db)["location"] == "Makati City"


def test_an_existing_location_is_never_overwritten(db):
    """The rule that keeps a teaser run from clobbering --full-desc data."""
    store(db, location="Makati City")
    healed = db.refresh_missing_fields(
        [{"job_key": "jobstreet:id:1", "location": "Somewhere Else"}])
    assert healed == 0
    assert stored(db)["location"] == "Makati City"


def test_several_fields_heal_in_one_pass(db):
    store(db, location="", salary="", listing_date="")
    healed = db.refresh_missing_fields([{
        "job_key": "jobstreet:id:1", "location": "Cebu",
        "salary": "PHP 60,000", "listing_date": "2026-08-01"}])
    assert healed == 1
    row = stored(db)
    assert row["location"] == "Cebu"
    assert row["salary"] == "PHP 60,000"
    assert row["listing_date"] == "2026-08-01"


def test_a_row_counts_once_however_many_fields_it_heals(db):
    store(db, location="", salary="")
    assert db.refresh_missing_fields([{
        "job_key": "jobstreet:id:1", "location": "Cebu",
        "salary": "PHP 60,000"}]) == 1


def test_an_empty_incoming_value_does_not_blank_a_stored_one(db):
    store(db, location="Makati City")
    db.refresh_missing_fields(
        [{"job_key": "jobstreet:id:1", "location": ""}])
    assert stored(db)["location"] == "Makati City"


def test_an_unknown_job_key_is_ignored(db):
    store(db)
    assert db.refresh_missing_fields(
        [{"job_key": "jobstreet:id:999", "location": "Nowhere"}]) == 0


def test_nothing_to_do_is_not_an_error(db):
    store(db, location="Makati City")
    assert db.refresh_missing_fields([]) == 0


def test_the_score_is_left_alone(db):
    """Healing a blank field must not disturb scoring or status."""
    store(db, location="", score_percent=73.0)
    db.refresh_missing_fields(
        [{"job_key": "jobstreet:id:1", "location": "Makati"}])
    row = stored(db)
    assert float(row["score_percent"]) == 73.0
    assert row["status"] == "new"

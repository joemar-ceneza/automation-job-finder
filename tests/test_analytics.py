"""
Tests for the pipeline analytics.

The funnel is measured by milestone depth (a skipped stage still counts every
rung it passed), and response rate is measured only over resolved applications
so a fresh batch of applications doesn't flatter or drag the number.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics
import config

NOW = datetime.datetime(2026, 7, 24, 12, 0, 0)


def event(job_key: str, stage: str, day: int) -> dict:
    return {"job_key": job_key, "stage": stage,
            "occurred_at": f"2026-07-{day:02d} 09:00:00"}


# ======================================================
# FUNNEL DEPTH
# ======================================================
def test_a_skipped_stage_still_counts_every_rung():
    """
    Milestone-depth model: reaching Offer implies passing every earlier rung,
    so an applied→offer job counts at applied, interviewed, AND offers. This
    keeps the funnel monotonic (applied >= interviewed >= offers).
    """
    events = [event("j1", "applied", 1), event("j1", "offer", 3)]
    funnel = analytics.compute(events, now=NOW)
    assert (funnel.applied, funnel.interviewed, funnel.offers) == (1, 1, 1)
    assert funnel.accepted == 0


def test_full_progression_counts_at_each_milestone():
    events = [event("j1", "applied", 1), event("j1", "technical interview", 3),
              event("j1", "offer", 5), event("j1", "accepted", 7)]
    funnel = analytics.compute(events, now=NOW)
    assert (funnel.applied, funnel.interviewed, funnel.offers,
            funnel.accepted) == (1, 1, 1, 1)


def test_a_job_never_applied_is_excluded():
    events = [event("j1", "saved", 1), event("j1", "interested", 2)]
    funnel = analytics.compute(events, now=NOW)
    assert funnel.applied == 0


# ======================================================
# RESPONSE RATE (resolved only)
# ======================================================
def test_a_rejection_counts_as_a_response():
    events = [event("j1", "applied", 1), event("j1", "rejected", 3)]
    funnel = analytics.compute(events, now=NOW)
    assert funnel.responded == 1
    assert funnel.response_rate == 100.0


def test_a_recent_silent_application_is_pending_not_counted_against_you():
    # Applied 2 days ago, still waiting — should not count as no-response.
    events = [event("j1", "applied", 22)]
    funnel = analytics.compute(events, now=NOW)
    assert funnel.pending == 1
    assert funnel.responded == 0 and funnel.no_response == 0
    assert funnel.response_rate is None       # nothing resolved yet


def test_a_long_silent_application_counts_as_no_response():
    # Applied long ago with no reply — inferred ghosted, resolved, no response.
    events = [event("j1", "applied", 1)]
    old_now = datetime.datetime(2026, 9, 1, 12, 0, 0)
    funnel = analytics.compute(events, now=old_now)
    assert funnel.no_response == 1
    assert funnel.response_rate == 0.0


def test_response_rate_mixes_resolved_only():
    events = [
        event("j1", "applied", 1), event("j1", "phone interview", 3),  # responded
        event("j2", "applied", 1), event("j2", "rejected", 4),          # responded
        event("j3", "applied", 22),                                     # pending
    ]
    funnel = analytics.compute(events, now=NOW)
    assert funnel.responded == 2
    assert funnel.pending == 1
    # 2 responded of 2 resolved = 100%; the pending one is excluded.
    assert funnel.response_rate == 100.0


# ======================================================
# CONVERSION RATES
# ======================================================
def test_conversion_rates():
    events = [
        event("j1", "applied", 1), event("j1", "phone interview", 3),
        event("j1", "offer", 5),
        event("j2", "applied", 1), event("j2", "hr interview", 4),
        event("j3", "applied", 1),
        event("j4", "applied", 2),
    ]
    funnel = analytics.compute(events, now=NOW)
    # 4 applied, 2 interviewed, 1 offer, 0 accepted
    assert funnel.applied_to_interview == 50.0
    assert funnel.interview_to_offer == 50.0
    assert funnel.offer_to_accept == 0.0


# ======================================================
# WEEKLY VOLUME
# ======================================================
def test_weekly_applications_are_grouped_by_iso_week():
    events = [event("j1", "applied", 1), event("j2", "applied", 2),
              event("j3", "applied", 20)]
    funnel = analytics.compute(events, now=NOW)
    total = sum(count for _week, count in funnel.weekly_applications)
    assert total == 3
    assert len(funnel.weekly_applications) >= 1


def test_empty_history_is_all_zero():
    funnel = analytics.compute([], now=NOW)
    assert funnel.applied == 0
    assert funnel.response_rate is None
    assert funnel.weekly_applications == []
    assert funnel.lines  # still renders a readable (zeroed) summary


# ======================================================
# END TO END THROUGH THE DATABASE
# ======================================================
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    import db_handler
    db_handler.init_db()
    return db_handler


def _job_row(job_key: str) -> dict:
    return {"job_key": job_key, "title": "Python Developer", "company": "Acme",
            "location": "Manila", "url": "https://example.com",
            "source": "jobstreet", "salary": "", "salary_min": "",
            "salary_max": "", "work_arrangement": "", "listing_date": "",
            "status": "saved", "search_keyword": "python", "score_percent": 10.0,
            "matched_skills": "Python", "required_years": "", "description": ""}


def test_recorded_stages_feed_the_funnel(db):
    db.insert_jobs([_job_row("jobstreet:id:1"), _job_row("jobstreet:id:2")])
    # One job interviews, the other applies and is rejected.
    db.record_stage("jobstreet:id:1", "applied")
    db.record_stage("jobstreet:id:1", "technical interview")
    db.record_stage("jobstreet:id:2", "applied")
    db.record_stage("jobstreet:id:2", "rejected")

    funnel = analytics.compute(db.all_stage_events())
    assert funnel.applied == 2
    assert funnel.interviewed == 1
    # Both resolved (a technical interview and a rejection are both responses).
    assert funnel.responded == 2
    assert funnel.response_rate == 100.0

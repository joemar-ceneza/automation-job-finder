"""
Tests for smart notifications.

The quiet rules are the feature: an alert that fires on everything is one you
learn to ignore. Each rule must be able to veto, every suppression must be
reported with a reason, and a channel that fails must not cause a job to be
recorded as announced.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import notifications

DAYTIME = datetime.datetime(2026, 7, 25, 14, 0, 0)
NIGHT = datetime.datetime(2026, 7, 25, 23, 30, 0)


def job(key="j1", score=50.0, title="Python Developer") -> dict:
    return {"job_key": key, "title": title, "company": "Acme",
            "score_percent": score, "url": "https://example.com"}


# ======================================================
# QUIET HOURS
# ======================================================
def test_quiet_hours_wrap_midnight():
    assert notifications.in_quiet_hours(NIGHT, (22, 7)) is True
    assert notifications.in_quiet_hours(DAYTIME, (22, 7)) is False
    early = datetime.datetime(2026, 7, 25, 3, 0, 0)
    assert notifications.in_quiet_hours(early, (22, 7)) is True


def test_a_daytime_window_does_not_wrap():
    noon = datetime.datetime(2026, 7, 25, 12, 0, 0)
    assert notifications.in_quiet_hours(noon, (9, 17)) is True
    assert notifications.in_quiet_hours(NIGHT, (9, 17)) is False


def test_an_empty_window_disables_quiet_hours():
    assert notifications.in_quiet_hours(NIGHT, (0, 0)) is False


def test_nothing_is_selected_during_quiet_hours():
    plan = notifications.select([job(score=99)], now=NIGHT,
                                quiet_hours=(22, 7))
    assert plan.quiet is True
    assert plan.selected == []
    assert any("Quiet hours" in line for line in plan.lines)


# ======================================================
# SCORE, REPEAT AND VOLUME
# ======================================================
def test_a_low_scoring_job_is_suppressed_with_a_reason():
    plan = notifications.select([job(score=5)], now=DAYTIME, min_score=25)
    assert plan.selected == []
    assert any("below" in reason for reason in plan.suppressed)


def test_an_already_notified_job_is_never_repeated():
    plan = notifications.select([job(key="j1", score=90)], seen={"j1"},
                                now=DAYTIME)
    assert plan.selected == []
    assert plan.suppressed.get("already sent") == 1


def test_the_best_jobs_are_chosen_first_and_the_rest_held():
    jobs = [job(key=f"j{i}", score=float(i * 10)) for i in range(1, 6)]
    plan = notifications.select(jobs, now=DAYTIME, min_score=0, max_per_run=2)
    assert [selected["job_key"] for selected in plan.selected] == ["j5", "j4"]
    assert plan.suppressed.get("held for the next run") == 3


def test_a_good_new_job_is_selected():
    plan = notifications.select([job(score=80)], now=DAYTIME)
    assert plan.has_anything is True
    assert any("worth a look" in line for line in plan.lines)


def test_an_unscored_job_does_not_crash_the_rules():
    plan = notifications.select([{"job_key": "j1", "title": "x"}],
                                now=DAYTIME, min_score=1)
    assert plan.selected == []


def test_nothing_at_all_says_so():
    plan = notifications.select([], now=DAYTIME)
    assert plan.selected == []
    assert any("Nothing new" in line for line in plan.lines)


# ======================================================
# SENDING
# ======================================================
def test_no_channels_means_nothing_is_delivered():
    plan = notifications.select([job(score=80)], now=DAYTIME)
    assert notifications.send(plan, channels=[]) == []


def test_an_unknown_channel_is_ignored_not_fatal():
    plan = notifications.select([job(score=80)], now=DAYTIME)
    assert notifications.send(plan, channels=["carrier-pigeon"]) == []


def test_a_failing_channel_reports_no_delivery(monkeypatch):
    """A channel that raises must not be counted as delivered."""
    monkeypatch.setitem(notifications._CHANNELS, "boom",
                        lambda plan: (_ for _ in ()).throw(RuntimeError("nope")))
    plan = notifications.select([job(score=80)], now=DAYTIME)
    assert notifications.send(plan, channels=["boom"]) == []


def test_a_working_channel_is_reported_as_delivered(monkeypatch):
    monkeypatch.setitem(notifications._CHANNELS, "fake", lambda plan: True)
    plan = notifications.select([job(score=80)], now=DAYTIME)
    assert notifications.send(plan, channels=["fake"]) == ["fake"]


def test_an_empty_plan_is_never_sent(monkeypatch):
    calls = []
    monkeypatch.setitem(notifications._CHANNELS, "fake",
                        lambda plan: calls.append(1) or True)
    plan = notifications.select([], now=DAYTIME)
    assert notifications.send(plan, channels=["fake"]) == []
    assert calls == []


# ======================================================
# DEDUPLICATION THROUGH THE DATABASE
# ======================================================
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    import db_handler
    db_handler.init_db()
    return db_handler


def test_marking_notified_makes_a_job_seen(db):
    assert db.already_notified(["j1", "j2"]) == set()
    db.mark_notified(["j1"])
    assert db.already_notified(["j1", "j2"]) == {"j1"}


def test_marking_the_same_job_twice_is_harmless(db):
    db.mark_notified(["j1"])
    db.mark_notified(["j1"])
    assert db.already_notified(["j1"]) == {"j1"}


def test_the_second_run_announces_nothing_new(db):
    """The end-to-end dedup story: notify once, then stay quiet."""
    jobs = [job(key="j1", score=90)]
    first = notifications.select(
        jobs, seen=db.already_notified(["j1"]), now=DAYTIME)
    assert first.has_anything is True
    db.mark_notified([selected["job_key"] for selected in first.selected])

    second = notifications.select(
        jobs, seen=db.already_notified(["j1"]), now=DAYTIME)
    assert second.has_anything is False

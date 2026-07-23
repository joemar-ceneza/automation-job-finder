"""Tests for the application lifecycle state machine."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import stages
from stages import Stage


# ======================================================
# PARSING
# ======================================================
@pytest.mark.parametrize("stored, expected", [
    ("saved", Stage.SAVED),
    ("applied", Stage.APPLIED),
    ("APPLIED", Stage.APPLIED),
    ("  offer  ", Stage.OFFER),
    ("new", Stage.SAVED),            # legacy default
    ("no answer", Stage.GHOSTED),    # legacy wording
    ("", Stage.SAVED),
    (None, Stage.SAVED),
    ("nonsense", Stage.SAVED),
])
def test_parse_tolerates_legacy_and_junk(stored, expected):
    assert stages.parse(stored) is expected


# ======================================================
# TRANSITIONS
# ======================================================
def test_forward_progression_is_allowed():
    assert stages.can_move(Stage.SAVED, Stage.APPLIED)
    assert stages.can_move(Stage.APPLIED, Stage.PHONE_INTERVIEW)
    assert stages.can_move(Stage.FINAL_INTERVIEW, Stage.OFFER)
    assert stages.can_move(Stage.OFFER, Stage.ACCEPTED)


def test_cannot_skip_backwards():
    assert not stages.can_move(Stage.OFFER, Stage.APPLIED)
    assert not stages.can_move(Stage.APPLIED, Stage.SAVED)


def test_cannot_leap_to_accepted():
    """An offer has to exist before it can be accepted."""
    assert not stages.can_move(Stage.APPLIED, Stage.ACCEPTED)
    assert not stages.can_move(Stage.SAVED, Stage.ACCEPTED)
    assert not stages.can_move(Stage.TECHNICAL_INTERVIEW, Stage.ACCEPTED)


def test_any_interview_round_can_lead_straight_to_an_offer():
    """Employers skip rounds; the tracker must be able to record that."""
    for interview in (Stage.PHONE_INTERVIEW, Stage.TECHNICAL_INTERVIEW,
                      Stage.HR_INTERVIEW, Stage.FINAL_INTERVIEW):
        assert stages.can_move(interview, Stage.OFFER), interview


def test_terminal_stages_are_final():
    for terminal in (Stage.ACCEPTED, Stage.REJECTED, Stage.GHOSTED,
                     Stage.WITHDRAWN):
        assert stages.allowed_moves(terminal) == []
        assert not stages.can_move(terminal, Stage.APPLIED)


def test_any_active_stage_can_exit():
    """An application can die at any point."""
    for active in (Stage.SAVED, Stage.APPLIED, Stage.TECHNICAL_INTERVIEW,
                   Stage.OFFER):
        for exit_stage in (Stage.REJECTED, Stage.GHOSTED, Stage.WITHDRAWN):
            assert stages.can_move(active, exit_stage), f"{active} -> {exit_stage}"


def test_moving_to_the_same_stage_is_not_a_move():
    assert not stages.can_move(Stage.APPLIED, Stage.APPLIED)


def test_allowed_moves_are_in_board_order():
    moves = stages.allowed_moves(Stage.APPLIED)
    assert moves == [stage for stage in stages.BOARD_ORDER if stage in moves]


def test_every_stage_appears_on_the_board():
    assert set(stages.BOARD_ORDER) == set(Stage)


# ======================================================
# GHOSTED DETECTION
# ======================================================
def test_stalled_only_applies_while_awaiting_a_reply():
    long_wait = config.GHOSTED_AFTER_DAYS + 5
    assert stages.is_stalled(Stage.APPLIED, long_wait)
    assert stages.is_stalled(Stage.TECHNICAL_INTERVIEW, long_wait)
    # Saved is not awaiting anything — you simply have not applied.
    assert not stages.is_stalled(Stage.SAVED, long_wait)
    assert not stages.is_stalled(Stage.REJECTED, long_wait)


def test_stalled_respects_the_threshold():
    assert not stages.is_stalled(Stage.APPLIED, config.GHOSTED_AFTER_DAYS - 1)
    assert stages.is_stalled(Stage.APPLIED, config.GHOSTED_AFTER_DAYS)


def test_stalled_handles_unknown_age():
    assert not stages.is_stalled(Stage.APPLIED, None)

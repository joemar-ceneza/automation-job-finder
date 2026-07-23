"""
stages.py
The application lifecycle: the eleven stages a job moves through, which
transitions are legal, and how a silent employer is detected.

Pure logic — no database, no I/O. Both the dashboard and --set-status route
their changes through can_move() so an impossible transition is refused in one
place rather than three.
"""
from enum import StrEnum

import config


class Stage(StrEnum):
    """Where an application currently stands."""
    SAVED = "saved"
    INTERESTED = "interested"
    APPLIED = "applied"
    PHONE_INTERVIEW = "phone interview"
    TECHNICAL_INTERVIEW = "technical interview"
    HR_INTERVIEW = "hr interview"
    FINAL_INTERVIEW = "final interview"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    WITHDRAWN = "withdrawn"


# Stages a job can never move out of.
TERMINAL = frozenset({Stage.ACCEPTED, Stage.REJECTED, Stage.GHOSTED,
                      Stage.WITHDRAWN})

# Reachable from any active stage — an application can die at any point.
EXITS = frozenset({Stage.REJECTED, Stage.GHOSTED, Stage.WITHDRAWN})

# Stages that mean you are waiting on the employer, so silence is meaningful.
AWAITING_REPLY = frozenset({Stage.APPLIED, Stage.PHONE_INTERVIEW,
                            Stage.TECHNICAL_INTERVIEW, Stage.HR_INTERVIEW,
                            Stage.FINAL_INTERVIEW})

# Stages that count as a real response from the employer (for response rate).
RESPONDED = frozenset({Stage.PHONE_INTERVIEW, Stage.TECHNICAL_INTERVIEW,
                       Stage.HR_INTERVIEW, Stage.FINAL_INTERVIEW,
                       Stage.OFFER, Stage.ACCEPTED, Stage.REJECTED})

# Forward progression. Exits are added to every active stage by can_move().
#
# Interview rounds are a sequence but not a mandatory one: plenty of employers
# skip straight from a technical round to an offer, so every interview stage
# can reach OFFER. Modelling the strict ladder would only make the tracker
# refuse to record what actually happened.
FORWARD: dict[Stage, frozenset[Stage]] = {
    Stage.SAVED: frozenset({Stage.INTERESTED, Stage.APPLIED}),
    Stage.INTERESTED: frozenset({Stage.APPLIED}),
    Stage.APPLIED: frozenset({Stage.PHONE_INTERVIEW,
                              Stage.TECHNICAL_INTERVIEW,
                              Stage.HR_INTERVIEW, Stage.OFFER}),
    Stage.PHONE_INTERVIEW: frozenset({Stage.TECHNICAL_INTERVIEW,
                                      Stage.HR_INTERVIEW,
                                      Stage.FINAL_INTERVIEW, Stage.OFFER}),
    Stage.TECHNICAL_INTERVIEW: frozenset({Stage.HR_INTERVIEW,
                                          Stage.FINAL_INTERVIEW, Stage.OFFER}),
    Stage.HR_INTERVIEW: frozenset({Stage.TECHNICAL_INTERVIEW,
                                   Stage.FINAL_INTERVIEW, Stage.OFFER}),
    Stage.FINAL_INTERVIEW: frozenset({Stage.OFFER}),
    Stage.OFFER: frozenset({Stage.ACCEPTED}),
}

# Order used by the board and any stage-keyed chart.
BOARD_ORDER: tuple[Stage, ...] = (
    Stage.SAVED, Stage.INTERESTED, Stage.APPLIED, Stage.PHONE_INTERVIEW,
    Stage.TECHNICAL_INTERVIEW, Stage.HR_INTERVIEW, Stage.FINAL_INTERVIEW,
    Stage.OFFER, Stage.ACCEPTED, Stage.REJECTED, Stage.GHOSTED,
    Stage.WITHDRAWN,
)


# ======================================================
# PUBLIC API
# ======================================================
def parse(value: str | None) -> Stage:
    """
    Converts stored text to a Stage, tolerating the legacy 'new' and
    'no answer' values from before the lifecycle existed.
    """
    text = (value or "").strip().lower()
    if not text or text == "new":
        return Stage.SAVED
    if text == "no answer":
        return Stage.GHOSTED
    try:
        return Stage(text)
    except ValueError:
        return Stage.SAVED


def can_move(current: Stage, target: Stage) -> bool:
    """True when moving from current to target is a legal transition."""
    if current == target:
        return False
    if current in TERMINAL:
        return False
    return target in EXITS or target in FORWARD.get(current, frozenset())


def allowed_moves(current: Stage) -> list[Stage]:
    """Every stage reachable from current, in board order."""
    return [stage for stage in BOARD_ORDER if can_move(current, stage)]


def is_stalled(stage: Stage, days_since_change: int | None) -> bool:
    """
    True when an application has sat awaiting a reply long enough to be
    treated as ghosted. Nobody remembers to record a silence, so the board
    surfaces this as a suggestion rather than waiting to be told.
    """
    if stage not in AWAITING_REPLY or days_since_change is None:
        return False
    return days_since_change >= config.GHOSTED_AFTER_DAYS

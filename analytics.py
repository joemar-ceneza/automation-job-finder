"""
analytics.py
Weekly pipeline analytics — how far your applications get, the rates they
convert at, and how many you send each week. Computed from the recorded stage
history, deterministically; there is no AI here and none is needed, because the
honest answer to "how am I converting?" is arithmetic over what you tracked.

The funnel is measured by milestone depth, not by current stage, so a job that
jumped from Applied straight to an Offer still counts at every rung it passed.
Response rate is deliberately measured only over *resolved* applications —
ones that got a reply, a rejection, or went silent long enough to count as
ghosted — so a batch of applications you sent yesterday doesn't drag the rate
down while you're still waiting. That is what keeps the number honest rather
than flattering.
"""
import datetime
from dataclasses import dataclass, field

import stages
from stages import Stage

# How deep into the pipeline each stage sits. Response/exit stages carry the
# depth of the furthest active stage they imply — reaching an interview implies
# you applied — so a job's depth is the max over every stage it ever hit.
_MILESTONE_RANK = {
    Stage.APPLIED: 1,
    Stage.PHONE_INTERVIEW: 2,
    Stage.TECHNICAL_INTERVIEW: 2,
    Stage.HR_INTERVIEW: 2,
    Stage.FINAL_INTERVIEW: 2,
    Stage.OFFER: 3,
    Stage.ACCEPTED: 4,
}


@dataclass
class Funnel:
    """The application pipeline, its conversion rates, and weekly volume."""
    applied: int = 0
    interviewed: int = 0
    offers: int = 0
    accepted: int = 0
    responded: int = 0
    no_response: int = 0
    pending: int = 0
    response_rate: float | None = None
    applied_to_interview: float | None = None
    interview_to_offer: float | None = None
    offer_to_accept: float | None = None
    weekly_applications: list[tuple[str, int]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _parse(timestamp: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime((timestamp or "")[:19],
                                           "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _ratio(numerator: int, denominator: int) -> float | None:
    """A percentage, or None when there's nothing to divide by."""
    return round(numerator / denominator * 100, 1) if denominator else None


def _group_by_job(events: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(event["job_key"], []).append(event)
    return grouped


def _describe(funnel: Funnel) -> list[str]:
    lines = [
        f"Applied {funnel.applied}  →  Interviewed {funnel.interviewed}  →  "
        f"Offers {funnel.offers}  →  Accepted {funnel.accepted}",
    ]
    if funnel.response_rate is not None:
        resolved = funnel.responded + funnel.no_response
        lines.append(f"Response rate: {funnel.response_rate}% "
                     f"({funnel.responded} of {resolved} resolved; "
                     f"{funnel.pending} still pending)")
    else:
        lines.append("Response rate: not enough resolved applications yet.")

    conversions = []
    if funnel.applied_to_interview is not None:
        conversions.append(f"applied→interview {funnel.applied_to_interview}%")
    if funnel.interview_to_offer is not None:
        conversions.append(f"interview→offer {funnel.interview_to_offer}%")
    if funnel.offer_to_accept is not None:
        conversions.append(f"offer→accept {funnel.offer_to_accept}%")
    if conversions:
        lines.append("Conversion: " + ", ".join(conversions))

    if funnel.weekly_applications:
        recent = funnel.weekly_applications[-8:]
        lines.append("Applications per week: "
                     + ", ".join(f"{week} {count}" for week, count in recent))
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def compute(events: list[dict], now: datetime.datetime | None = None,
            weeks: int = 12) -> Funnel:
    """
    Builds the pipeline funnel from recorded stage events. Each event is
    {job_key, stage, occurred_at}. Pure over its inputs, so `now` is injectable
    for testing the stalled/pending boundary.
    """
    now = now or datetime.datetime.now()
    funnel = Funnel()
    weekly: dict[str, int] = {}

    for job_events in _group_by_job(events).values():
        stages_seen = {stages.parse(event["stage"]) for event in job_events}
        depth = max((_MILESTONE_RANK.get(stage, 0) for stage in stages_seen),
                    default=0)
        if depth < 1:
            continue                                    # never applied

        funnel.applied += 1
        funnel.interviewed += depth >= 2
        funnel.offers += depth >= 3
        funnel.accepted += depth >= 4

        responded = bool(stages_seen & stages.RESPONDED)
        latest = max(job_events, key=lambda event: event["occurred_at"])
        current = stages.parse(latest["stage"])
        last_seen = _parse(latest["occurred_at"])
        days_idle = (now - last_seen).days if last_seen else None

        if responded:
            funnel.responded += 1
        elif current in stages.AWAITING_REPLY and not stages.is_stalled(
                current, days_idle):
            funnel.pending += 1                         # still waiting, fairly
        else:
            funnel.no_response += 1                     # silent or withdrawn

        dates = [_parse(event["occurred_at"]) for event in job_events]
        earliest = min((date for date in dates if date), default=None)
        if earliest:
            iso = earliest.isocalendar()
            week = f"{iso.year}-W{iso.week:02d}"
            weekly[week] = weekly.get(week, 0) + 1

    resolved = funnel.responded + funnel.no_response
    funnel.response_rate = _ratio(funnel.responded, resolved)
    funnel.applied_to_interview = _ratio(funnel.interviewed, funnel.applied)
    funnel.interview_to_offer = _ratio(funnel.offers, funnel.interviewed)
    funnel.offer_to_accept = _ratio(funnel.accepted, funnel.offers)
    funnel.weekly_applications = sorted(weekly.items())[-weeks:]
    funnel.lines = _describe(funnel)
    return funnel

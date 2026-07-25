"""
notifications.py
Smart notifications — telling you about a job worth opening, and staying silent
otherwise.

The quiet rules are the feature, not a setting on it. An alert that fires on
every scraped listing is one you learn to ignore within a week, and an ignored
alert is worse than none: it costs attention and returns nothing. So four rules
apply before anything is sent, and each can veto:

  1. Score  — below NOTIFY_MIN_SCORE it is not worth interrupting you for.
  2. Repeat — a job you have already been told about is never announced twice.
  3. Volume — at most NOTIFY_MAX_PER_RUN per run; the rest wait.
  4. Hours  — nothing during quiet hours, because a job advert is not urgent.

Selection is pure and deterministic, and every suppression is reported with its
reason — a notifier that silently drops things is impossible to trust or debug.
Sending is separate, so the rules can be tested without a channel, and a channel
failure can never lose the record of what was chosen.
"""
import datetime
import logging
from dataclasses import dataclass, field

import config


@dataclass
class NotificationPlan:
    """What would be announced, and why everything else was not."""
    selected: list[dict] = field(default_factory=list)
    suppressed: dict[str, int] = field(default_factory=dict)  # reason -> count
    quiet: bool = False
    lines: list[str] = field(default_factory=list)

    @property
    def has_anything(self) -> bool:
        return bool(self.selected)


# ======================================================
# THE QUIET RULES
# ======================================================
def in_quiet_hours(now: datetime.datetime,
                   window: tuple[int, int] | None = None) -> bool:
    """
    True inside the do-not-disturb window. A window whose start is later than
    its end wraps midnight (22 -> 7 is 10pm until 7am), which is the shape
    almost everyone actually wants.
    """
    start, end = window or config.NOTIFY_QUIET_HOURS
    if start == end:
        return False                    # an empty window disables quiet hours
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _score(job: dict) -> float:
    try:
        return float(job.get("score_percent") or 0)
    except (TypeError, ValueError):
        return 0.0


def _describe(plan: NotificationPlan) -> list[str]:
    if plan.quiet:
        return ["Quiet hours — nothing sent. It will keep until morning."]
    if not plan.selected:
        if not plan.suppressed:
            return ["Nothing new to tell you about."]
        reasons = ", ".join(f"{count} {reason}"
                            for reason, count in sorted(plan.suppressed.items()))
        return [f"Nothing worth notifying about ({reasons})."]

    lines = [f"{len(plan.selected)} job(s) worth a look:"]
    for job in plan.selected:
        lines.append(f"  {_score(job):.0f}% — {job.get('title', '')} @ "
                     f"{job.get('company') or 'unknown'}")
    if plan.suppressed:
        reasons = ", ".join(f"{count} {reason}"
                            for reason, count in sorted(plan.suppressed.items()))
        lines.append(f"(held back: {reasons})")
    return lines


# ======================================================
# SELECTION
# ======================================================
def select(jobs: list[dict], seen: set[str] | None = None,
           now: datetime.datetime | None = None,
           min_score: float | None = None,
           max_per_run: int | None = None,
           quiet_hours: tuple[int, int] | None = None) -> NotificationPlan:
    """
    Decides what to announce. Pure: `seen` is the job keys already notified and
    `now` is injectable, so the rules are testable without a clock or a
    database. Everything filtered out is counted under a reason.
    """
    now = now or datetime.datetime.now()
    seen = seen or set()
    min_score = config.NOTIFY_MIN_SCORE if min_score is None else min_score
    max_per_run = (config.NOTIFY_MAX_PER_RUN if max_per_run is None
                   else max_per_run)

    plan = NotificationPlan()
    if in_quiet_hours(now, quiet_hours):
        plan.quiet = True
        plan.lines = _describe(plan)
        return plan

    def count(reason: str) -> None:
        plan.suppressed[reason] = plan.suppressed.get(reason, 0) + 1

    candidates = []
    for job in jobs:
        if job.get("job_key") in seen:
            count("already sent")
            continue
        if _score(job) < min_score:
            count(f"below {min_score:.0f}%")
            continue
        candidates.append(job)

    candidates.sort(key=_score, reverse=True)
    plan.selected = candidates[:max_per_run]
    held = len(candidates) - len(plan.selected)
    if held > 0:
        plan.suppressed["held for the next run"] = held

    plan.lines = _describe(plan)
    return plan


# ======================================================
# CHANNELS
# ======================================================
def _send_desktop(plan: NotificationPlan) -> bool:
    """
    A Windows toast. win11toast is optional and imported lazily, exactly like
    the AI clients: without it the feature says so once and the other channels
    still work.
    """
    try:
        from win11toast import notify as toast
    except ImportError:
        logging.info("Desktop notifications need win11toast — install it with "
                     "`pip install win11toast`, or drop \"desktop\" from "
                     "NOTIFY_CHANNELS.")
        return False

    best = plan.selected[0]
    others = len(plan.selected) - 1
    body = (f"{_score(best):.0f}% — {best.get('title', '')} @ "
            f"{best.get('company') or 'unknown'}")
    if others:
        body += f"\n…and {others} more"
    try:
        toast("New job matches", body)
        return True
    except Exception as error:                      # a toast must never crash a run
        logging.warning("Desktop notification failed: %s", error)
        return False


def _send_email(plan: NotificationPlan) -> bool:
    """Reuses the existing Gmail digest rather than inventing a second mailer."""
    import email_handler
    return email_handler.run_email_digest(plan.selected)


_CHANNELS = {"desktop": _send_desktop, "email": _send_email}


def send(plan: NotificationPlan,
         channels: list[str] | None = None) -> list[str]:
    """
    Delivers a plan over the configured channels. Returns the channels that
    actually succeeded, so the caller only records jobs as notified when at
    least one of them worked — a failed send must not silence a job forever.
    """
    channels = config.NOTIFY_CHANNELS if channels is None else channels
    if not plan.has_anything or not channels:
        return []

    delivered = []
    for name in channels:
        sender = _CHANNELS.get(name)
        if sender is None:
            logging.warning("Unknown notification channel '%s' — ignoring.",
                            name)
            continue
        try:
            if sender(plan):
                delivered.append(name)
        except Exception as error:      # one bad channel must not stop the rest
            logging.error("Notification channel '%s' failed: %s", name, error)
    return delivered

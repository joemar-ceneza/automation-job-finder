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
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

import config

load_dotenv()

# Telegram hard-caps a message at 4096 characters and rejects the whole thing
# if it goes over, so blocks are packed into chunks below that with headroom.
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_LIMIT = 3900

# Every one of these is a MarkdownV2 control character. An unescaped one in a
# job title — "Node_JS Developer", "C* Engineer", "Dev [Remote]" — makes
# Telegram reject the entire message with a 400, so they are escaped everywhere
# text is interpolated.
_MARKDOWN_V2_SPECIALS = r"_*[]()~`>#+-=|{}.!"
_ESCAPE_TABLE = str.maketrans({char: "\\" + char for char in _MARKDOWN_V2_SPECIALS})


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


def _escape(value: object) -> str:
    """Escapes MarkdownV2 control characters so Telegram cannot reject a job title."""
    return str(value or "").translate(_ESCAPE_TABLE)


def _telegram_blocks(plan: NotificationPlan) -> list[str]:
    """One formatted block per job. Kept separate so it can be tested offline."""
    blocks = []
    for job in plan.selected:
        lines = [f"*{_escape(f'{_score(job):.0f}%')}* — {_escape(job.get('title') or 'Untitled')}",
                 f"📍 {_escape(job.get('company') or 'Unknown')}"]
        if job.get("location"):
            lines.append(f"   {_escape(job['location'])}")
        if job.get("salary"):
            lines.append(f"💰 {_escape(job['salary'])}")
        if job.get("url"):
            # Inside a link target only ')' and '\' are special — escaping the
            # rest here would corrupt the URL.
            target = str(job["url"]).replace("\\", "\\\\").replace(")", "\\)")
            lines.append(f"🔗 [View Job]({target})")
        blocks.append("\n".join(lines))
    return blocks


def _chunk(blocks: list[str], header: str,
           limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """
    Packs job blocks into messages under Telegram's size cap, splitting only
    between jobs so a block is never cut mid-entity (which would 400).
    """
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def _post_telegram(token: str, chat_id: str, text: str,
                   markdown: bool = True) -> None:
    """Posts one message. Raises on any transport or API error."""
    import httpx

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if markdown:
        payload["parse_mode"] = "MarkdownV2"
    reply = httpx.post(_TELEGRAM_API.format(token=token), json=payload,
                       timeout=15)
    reply.raise_for_status()


def _send_telegram(plan: NotificationPlan) -> bool:
    """
    Sends the plan to a Telegram chat. Needs TELEGRAM_BOT_TOKEN (from
    @BotFather) and TELEGRAM_CHAT_ID in .env.

    Formatting is the whole risk here. Telegram rejects an entire message when
    its Markdown does not parse, and because a rejected send means the jobs are
    never recorded as announced, the same message would be rebuilt and rejected
    on every future run — one job title with an underscore in it could silence
    notifications permanently. So text is escaped, long batches are split, and
    a formatting failure falls back to plain text rather than giving up.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logging.info("Telegram notifications need TELEGRAM_BOT_TOKEN and "
                     "TELEGRAM_CHAT_ID in .env — see .env.example.")
        return False

    header = f"🔔 *New Job Matches* \\({len(plan.selected)}\\)"
    messages = _chunk(_telegram_blocks(plan), header)

    sent = 0
    for message in messages:
        try:
            _post_telegram(token, chat_id, message)
            sent += 1
        except Exception as error:
            # Retrying identical Markdown cannot succeed, so drop the formatting
            # rather than the notification.
            logging.warning("Telegram rejected a formatted message (%s) — "
                            "retrying as plain text.", error)
            try:
                _post_telegram(token, chat_id, _plain(message), markdown=False)
                sent += 1
            except Exception as fallback_error:
                logging.error("Telegram send failed: %s", fallback_error)

    if sent:
        logging.info("Telegram notification sent (%d message(s)).", sent)
    return sent > 0


def _plain(message: str) -> str:
    """Strips the MarkdownV2 escaping and emphasis for the fallback send."""
    for char in _MARKDOWN_V2_SPECIALS:
        message = message.replace("\\" + char, char)
    return message.replace("*", "")


_CHANNELS = {"desktop": _send_desktop, "email": _send_email, "telegram": _send_telegram}


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

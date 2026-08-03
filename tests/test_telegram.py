"""
Tests for the Telegram notification channel.

Formatting is the whole risk here, and the failure it causes is worse than it
looks. Telegram rejects an *entire* message when its Markdown does not parse,
and a rejected send means `send()` reports no delivery, which means the caller
never records those jobs as announced — so the next run rebuilds the same
message and it is rejected again. One job title containing an underscore could
silence notifications permanently, with nothing but a warning in the log.

So these tests are mostly about hostile job titles, which are not hypothetical:
"Node_JS Developer", "C# / C++ Engineer" and "Developer [Remote]" are ordinary
postings.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notifications

# Every character Telegram treats as MarkdownV2 syntax.
SPECIALS = r"_*[]()~`>#+-=|{}.!"


def job(title="Python Developer", **overrides) -> dict:
    base = {"job_key": "j1", "title": title, "company": "Acme",
            "score_percent": 90.0, "url": "https://example.com/job/1"}
    base.update(overrides)
    return base


def plan_for(*jobs) -> notifications.NotificationPlan:
    return notifications.NotificationPlan(selected=list(jobs))


def unescaped_specials(text: str) -> list[str]:
    """Special characters that are not preceded by a backslash."""
    found = []
    for index, char in enumerate(text):
        if char not in SPECIALS:
            continue
        if index and text[index - 1] == "\\":
            continue
        found.append(char)
    return found


# ======================================================
# ESCAPING — the permanent-wedge bug
# ======================================================
@pytest.mark.parametrize("value", [
    "Senior Node_JS Developer",
    "C* Engineer",
    "Developer [Remote]",
    "Backend Dev (Python) - urgent!",
    "Data Engineer #2 | 100% remote",
    "Dev `backtick` role",
    "Full-Stack ~ Engineer",
    "Acme *Corp* (PH) Inc.",
    "PHP 50,000 - 90,000",
])
def test_escape_leaves_no_special_unescaped(value):
    """Any one of these unescaped would make Telegram 400 the whole batch."""
    assert unescaped_specials(notifications._escape(value)) == []


@pytest.mark.parametrize("title", [
    "Senior Node_JS Developer",
    "C* Engineer",
    "Developer [Remote]",
    "Backend Dev (Python) - urgent!",
    "Data Engineer #2 | 100% remote",
])
def test_a_hostile_title_reaches_the_message_escaped(title):
    """The emphasis markers are ours; everything interpolated must be escaped."""
    block = notifications._telegram_blocks(plan_for(job(title=title)))[0]
    assert notifications._escape(title) in block
    assert title not in block          # i.e. it was not passed through raw


def test_the_company_name_is_escaped_too():
    company = "Acme *Corp* (PH) Inc."
    block = notifications._telegram_blocks(plan_for(job(company=company)))[0]
    assert notifications._escape(company) in block


def test_salary_and_location_are_escaped():
    block = notifications._telegram_blocks(
        plan_for(job(salary="PHP 50,000 - 90,000",
                     location="Makati (Metro Manila)")))[0]
    assert notifications._escape("PHP 50,000 - 90,000") in block
    assert notifications._escape("Makati (Metro Manila)") in block


def test_only_our_own_emphasis_markers_survive_unescaped():
    """
    The score is deliberately bold. Nothing else in the text may carry a bare
    '*', or the message stops parsing.
    """
    block = notifications._telegram_blocks(
        plan_for(job(title="C* Engineer", company="A*B")))[0]
    text = block.split("🔗")[0]
    assert unescaped_specials(text) == ["*", "*"]


def test_a_url_keeps_its_own_characters():
    """
    Escaping the whole URL would corrupt it. Only ')' and '\\' are special
    inside a MarkdownV2 link target.
    """
    block = notifications._telegram_blocks(
        plan_for(job(url="https://ph.jobstreet.com/job/123?src=a&b=2")))[0]
    assert "https://ph.jobstreet.com/job/123?src=a&b=2" in block


def test_a_closing_paren_in_a_url_is_escaped():
    """An unescaped ')' would end the link early and break the message."""
    block = notifications._telegram_blocks(
        plan_for(job(url="https://example.com/a(b)c")))[0]
    assert "https://example.com/a(b\\)c" in block


def test_a_missing_title_does_not_produce_none():
    block = notifications._telegram_blocks(plan_for(job(title="")))[0]
    assert "Untitled" in block
    assert "None" not in block


# ======================================================
# CHUNKING — Telegram's 4096 character cap
# ======================================================
def test_a_long_batch_is_split_below_the_cap():
    jobs = [job(title=f"Developer number {n}", company="A" * 200)
            for n in range(60)]
    messages = notifications._chunk(
        notifications._telegram_blocks(plan_for(*jobs)), "HDR")
    assert len(messages) > 1
    assert all(len(message) <= notifications._TELEGRAM_LIMIT
               for message in messages)


def test_splitting_never_cuts_a_job_in_half():
    """
    A block split mid-entity would produce unbalanced markup, which is the
    exact thing the escaping exists to prevent.
    """
    jobs = [job(title=f"Dev {n}", company="B" * 300) for n in range(40)]
    blocks = notifications._telegram_blocks(plan_for(*jobs))
    messages = notifications._chunk(blocks, "HDR")
    rejoined = "\n\n".join(messages)
    for block in blocks:
        assert block in rejoined


def test_a_small_batch_stays_one_message():
    messages = notifications._chunk(
        notifications._telegram_blocks(plan_for(job(), job(key := "j2"))), "HDR")
    assert len(messages) == 1


# ======================================================
# SENDING — degradation, not silence
# ======================================================
def test_a_formatting_rejection_falls_back_to_plain_text(monkeypatch):
    """
    Retrying identical Markdown cannot succeed, so the notification must go out
    unformatted rather than not at all — otherwise the jobs are never recorded
    as sent and the same failure repeats forever.
    """
    attempts = []

    def fake_post(token, chat_id, text, markdown=True):
        attempts.append(markdown)
        if markdown:
            raise RuntimeError("400 Bad Request: can't parse entities")

    monkeypatch.setattr(notifications, "_post_telegram", fake_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    assert notifications._send_telegram(plan_for(job())) is True
    assert attempts == [True, False]


def test_a_total_failure_reports_false(monkeypatch):
    """So the caller does not record the jobs as announced."""
    def always_fail(token, chat_id, text, markdown=True):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications, "_post_telegram", always_fail)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    assert notifications._send_telegram(plan_for(job())) is False


def test_missing_credentials_are_reported_not_raised(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notifications._send_telegram(plan_for(job())) is False


def test_the_plain_fallback_strips_escaping_and_emphasis():
    formatted = notifications._telegram_blocks(
        plan_for(job(title="Node_JS *Dev*")))[0]
    plain = notifications._plain(formatted)
    assert "Node_JS" in plain
    assert "\\" not in plain
    assert "*" not in plain


def test_telegram_is_a_registered_channel():
    assert "telegram" in notifications._CHANNELS

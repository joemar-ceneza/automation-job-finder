"""
email_handler.py
Sends an email digest of newly found job matches via Gmail SMTP.

Credentials come from .env (see .env.example):
    GMAIL_ADDRESS       — sender Gmail account
    GMAIL_APP_PASSWORD  — Google App Password (not the normal password)
    EMAIL_RECIPIENT     — optional; defaults to GMAIL_ADDRESS
"""
import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

import config


# ======================================================
# CREDENTIALS
# ======================================================
def _load_credentials() -> tuple[str, str, str] | None:
    """
    Loads (sender, app_password, recipient) from .env.
    Returns None (with a clear log message) when anything is missing.
    """
    load_dotenv(os.path.join(config.BASE_DIR, ".env"))
    sender = os.getenv("GMAIL_ADDRESS", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.getenv("EMAIL_RECIPIENT", "").strip() or sender

    if not sender or not app_password:
        logging.error("Email digest skipped: GMAIL_ADDRESS and/or "
                      "GMAIL_APP_PASSWORD missing from .env "
                      "(copy .env.example to .env and fill them in).")
        return None
    return sender, app_password, recipient


# ======================================================
# DIGEST BODY
# ======================================================
def _build_digest_html(rows: list[dict]) -> str:
    """Renders the new job matches as a compact HTML email body."""
    items = []
    for row in rows[:config.EMAIL_MAX_ROWS]:
        salary = f" — {html.escape(row['salary'])}" if row.get("salary") else ""
        arrangement = (f" [{html.escape(row['work_arrangement'])}]"
                       if row.get("work_arrangement") else "")
        items.append(
            f"<li><b>{row.get('score_percent', '')}%</b> — "
            f"<a href=\"{html.escape(str(row.get('url', '')), quote=True)}\">"
            f"{html.escape(str(row.get('title', '')))}</a> @ "
            f"{html.escape(str(row.get('company', '')))}"
            f"{arrangement}{salary}<br>"
            f"<small>{html.escape(str(row.get('matched_skills', '')))}</small></li>"
        )
    hidden = len(rows) - config.EMAIL_MAX_ROWS
    more_note = (f"<p>...and {hidden} more in the full report.</p>"
                 if hidden > 0 else "")
    return (
        f"<html><body><p>{len(rows)} new job match(es) found:</p>"
        f"<ol>{''.join(items)}</ol>{more_note}"
        "<p><small>Full details: output/ranked_jobs.csv and "
        "output/report.html</small></p></body></html>"
    )


# ======================================================
# PUBLIC ENTRY POINT
# ======================================================
def run_email_digest(rows: list[dict]) -> bool:
    """
    Emails the given new job matches via Gmail SMTP.
    Returns True on success, False when skipped or failed.
    """
    if not rows:
        logging.info("No new matches — email digest not sent.")
        return False

    credentials = _load_credentials()
    if credentials is None:
        return False
    sender, app_password, recipient = credentials

    message = MIMEMultipart("alternative")
    message["Subject"] = (f"{config.EMAIL_SUBJECT_PREFIX} "
                          f"{len(rows)} new job match(es)")
    message["From"] = sender
    message["To"] = recipient
    message.attach(MIMEText(_build_digest_html(rows), "html", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, app_password)
            smtp.sendmail(sender, [recipient], message.as_string())
        logging.info("Email digest with %d job(s) sent to %s.",
                     len(rows), recipient)
        return True
    except Exception as e:
        logging.error("Failed to send email digest: %s", e)
        return False

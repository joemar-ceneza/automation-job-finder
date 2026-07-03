"""
db_handler.py
SQLite persistence for scraped/scored jobs. Lets the pipeline track new
listings over time, skip re-scoring jobs that were already seen, record
application status, archive stale listings, and re-score stored jobs when
the skill list changes.
"""
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key           TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    company           TEXT,
    location          TEXT,
    url               TEXT,
    salary            TEXT,
    salary_min        REAL,
    salary_max        REAL,
    work_arrangement  TEXT,
    listing_date      TEXT,
    status            TEXT DEFAULT 'new',
    archived          INTEGER DEFAULT 0,
    search_keyword    TEXT,
    score_percent     REAL,
    matched_skills    TEXT,
    required_years    INTEGER,
    description       TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL
)
"""

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
)
"""

# Columns added after the first release — migrated in init_db() so existing
# databases keep working. Maps column name -> ALTER TABLE type/default.
_MIGRATED_COLUMNS = {
    "salary": "TEXT",
    "salary_min": "REAL",
    "salary_max": "REAL",
    "work_arrangement": "TEXT",
    "listing_date": "TEXT",
    "status": "TEXT DEFAULT 'new'",
    "archived": "INTEGER DEFAULT 0",
}


# ======================================================
# CONNECTION HELPERS
# ======================================================
def _connect() -> sqlite3.Connection:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    connection = sqlite3.connect(config.DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ======================================================
# PUBLIC API — SETUP
# ======================================================
def init_db() -> None:
    """Creates the jobs/meta tables if missing and adds missing columns."""
    with closing(_connect()) as connection, connection:
        connection.execute(_SCHEMA)
        connection.execute(_META_SCHEMA)
        columns = {row["name"] for row in
                   connection.execute("PRAGMA table_info(jobs)")}
        for column, column_type in _MIGRATED_COLUMNS.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE jobs ADD COLUMN {column} {column_type}")
                logging.info("Migrated database: added %s column.", column)


# ======================================================
# PUBLIC API — SCRAPE TRACKING
# ======================================================
def get_existing_keys(job_keys: list[str]) -> set[str]:
    """Returns the subset of job_keys already stored in the database."""
    if not job_keys:
        return set()
    placeholders = ",".join("?" for _ in job_keys)
    with closing(_connect()) as connection:
        rows = connection.execute(
            f"SELECT job_key FROM jobs WHERE job_key IN ({placeholders})",
            job_keys,
        ).fetchall()
    return {row["job_key"] for row in rows}


def insert_jobs(rows: list[dict]) -> None:
    """Inserts newly scored jobs with first_seen/last_seen set to now."""
    if not rows:
        return
    now = _now()
    with closing(_connect()) as connection, connection:
        connection.executemany(
            """INSERT OR REPLACE INTO jobs
               (job_key, title, company, location, url, salary,
                salary_min, salary_max, work_arrangement, listing_date,
                status, archived, search_keyword, score_percent,
                matched_skills, required_years, description,
                first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (row["job_key"], row["title"], row["company"], row["location"],
                 row["url"], row.get("salary", ""),
                 row.get("salary_min") or None, row.get("salary_max") or None,
                 row.get("work_arrangement", ""), row.get("listing_date", ""),
                 row.get("status", "new"), row.get("search_keyword", ""),
                 row["score_percent"], row["matched_skills"],
                 row["required_years"] or None,
                 row.get("description", ""), now, now)
                for row in rows
            ],
        )
    logging.info("Inserted %d new jobs into %s", len(rows), config.DB_PATH)


def mark_seen(job_keys: list[str]) -> None:
    """Updates last_seen (and un-archives) jobs that reappeared this run."""
    if not job_keys:
        return
    placeholders = ",".join("?" for _ in job_keys)
    with closing(_connect()) as connection, connection:
        connection.execute(
            f"""UPDATE jobs SET last_seen = ?, archived = 0
                WHERE job_key IN ({placeholders})""",
            [_now(), *job_keys],
        )
    logging.info("Updated last_seen for %d already-seen jobs.", len(job_keys))


def fetch_jobs(job_keys: list[str]) -> list[dict]:
    """Returns stored rows (with their original scores) for the given keys."""
    if not job_keys:
        return []
    placeholders = ",".join("?" for _ in job_keys)
    with closing(_connect()) as connection:
        rows = connection.execute(
            f"SELECT * FROM jobs WHERE job_key IN ({placeholders})",
            job_keys,
        ).fetchall()
    return [dict(row) for row in rows]


# ======================================================
# PUBLIC API — APPLICATION STATUS
# ======================================================
_JOB_URL_ID_PATTERN = re.compile(r"/job/(\d+)")


def _normalize_job_key(key_or_url: str) -> str:
    """Accepts a stored job_key OR a JobStreet job URL and returns the key."""
    id_match = _JOB_URL_ID_PATTERN.search(key_or_url)
    if id_match:
        return f"id:{id_match.group(1)}"
    return key_or_url


def set_status(key_or_url: str, status: str) -> bool:
    """
    Records what you did with a job (e.g. interested/applied/rejected).
    Accepts the job_key or the job's URL.
    Returns False when the job isn't in the database.
    """
    job_key = _normalize_job_key(key_or_url)
    with closing(_connect()) as connection, connection:
        cursor = connection.execute(
            "UPDATE jobs SET status = ? WHERE job_key = ?", (status, job_key))
    if cursor.rowcount == 0:
        logging.error("No job with key '%s' in the database.", job_key)
        return False
    logging.info("Status of %s set to '%s'.", job_key, status)
    return True


# ======================================================
# PUBLIC API — MAINTENANCE (PRUNE / RESCORE)
# ======================================================
def prune_stale(days: int) -> int:
    """
    Archives (does NOT delete) jobs not seen in the last N days, so they
    stop appearing in exports. Re-appearing jobs are un-archived by
    mark_seen(). Returns the number of jobs archived.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with closing(_connect()) as connection, connection:
        cursor = connection.execute(
            "UPDATE jobs SET archived = 1 WHERE archived = 0 AND last_seen < ?",
            (cutoff,),
        )
    logging.info("Archived %d jobs not seen in the last %d days.",
                 cursor.rowcount, days)
    return cursor.rowcount


def fetch_all_active() -> list[dict]:
    """Returns every non-archived stored job (used by --rescore)."""
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM jobs WHERE archived = 0").fetchall()
    return [dict(row) for row in rows]


def update_scores(rows: list[dict]) -> None:
    """Overwrites stored scores/matched skills after a --rescore run."""
    if not rows:
        return
    with closing(_connect()) as connection, connection:
        connection.executemany(
            """UPDATE jobs SET score_percent = ?, matched_skills = ?
               WHERE job_key = ?""",
            [(row["score_percent"], row["matched_skills"], row["job_key"])
             for row in rows],
        )
    logging.info("Re-scored %d stored jobs.", len(rows))


# ======================================================
# PUBLIC API — META (key/value settings, e.g. skills hash)
# ======================================================
def get_meta(key: str) -> str | None:
    """Reads a value from the meta table, or None when unset."""
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    """Writes a value to the meta table."""
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )

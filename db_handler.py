"""
db_handler.py
SQLite persistence for scraped/scored jobs. Lets the pipeline track new
listings over time and skip re-scoring jobs that were already seen.
"""
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key         TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    url             TEXT,
    salary          TEXT,
    search_keyword  TEXT,
    score_percent   REAL,
    matched_skills  TEXT,
    required_years  INTEGER,
    description     TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
)
"""


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
# PUBLIC API
# ======================================================
def init_db() -> None:
    """Creates the jobs table if it doesn't exist and adds missing columns."""
    with closing(_connect()) as connection, connection:
        connection.execute(_SCHEMA)
        columns = {row["name"] for row in
                   connection.execute("PRAGMA table_info(jobs)")}
        if "salary" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN salary TEXT")
            logging.info("Migrated database: added salary column.")


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


def insert_jobs(rows: list[dict], search_keyword: str) -> None:
    """Inserts newly scored jobs with first_seen/last_seen set to now."""
    if not rows:
        return
    now = _now()
    with closing(_connect()) as connection, connection:
        connection.executemany(
            """INSERT OR REPLACE INTO jobs
               (job_key, title, company, location, url, salary,
                search_keyword, score_percent, matched_skills,
                required_years, description, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (row["job_key"], row["title"], row["company"], row["location"],
                 row["url"], row.get("salary", ""), search_keyword,
                 row["score_percent"], row["matched_skills"],
                 row["required_years"] or None,
                 row.get("description", ""), now, now)
                for row in rows
            ],
        )
    logging.info("Inserted %d new jobs into %s", len(rows), config.DB_PATH)


def mark_seen(job_keys: list[str]) -> None:
    """Updates last_seen for jobs that reappeared in this run."""
    if not job_keys:
        return
    placeholders = ",".join("?" for _ in job_keys)
    with closing(_connect()) as connection, connection:
        connection.execute(
            f"UPDATE jobs SET last_seen = ? WHERE job_key IN ({placeholders})",
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

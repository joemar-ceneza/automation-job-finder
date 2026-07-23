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
import stages

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key           TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    company           TEXT,
    location          TEXT,
    url               TEXT,
    source            TEXT,
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

# One row per skill found in one advertisement — the source for all demand
# analytics. Rebuilt whenever a job is (re)scored.
_JOB_SKILLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_skills (
    job_key   TEXT NOT NULL,
    skill     TEXT NOT NULL,
    category  TEXT,
    in_title  INTEGER DEFAULT 0,
    PRIMARY KEY (job_key, skill)
)
"""

# Append-only history of every stage change. jobs.status holds the current
# stage as a denormalised head so list views need no correlated subquery.
_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_events (
    id          INTEGER PRIMARY KEY,
    job_key     TEXT NOT NULL,
    stage       TEXT NOT NULL,
    note        TEXT,
    occurred_at TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills(skill)",
    "CREATE INDEX IF NOT EXISTS idx_job_skills_cat   ON job_skills(category)",
    "CREATE INDEX IF NOT EXISTS idx_events_job       ON application_events(job_key)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_score       ON jobs(score_percent)",
)

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
    "source": "TEXT",
    "status_changed_at": "TEXT",
    "notes": "TEXT",
    "duplicate_of": "TEXT",
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
# PUBLIC API — BACKUP
# ======================================================
def _prune_backups() -> None:
    """Deletes the oldest backups beyond config.BACKUP_KEEP."""
    if not os.path.isdir(config.BACKUP_DIR):
        return
    backups = sorted(
        (entry.path for entry in os.scandir(config.BACKUP_DIR)
         if entry.name.startswith("jobs_") and entry.name.endswith(".db")),
        reverse=True,
    )
    for stale in backups[config.BACKUP_KEEP:]:
        os.remove(stale)
        logging.info("Removed old backup %s", stale)


def backup_database(reason: str = "manual") -> str | None:
    """
    Copies the database to output/backups/ using SQLite's online backup API,
    which is safe even if the file is being written. Returns the backup path,
    or None when there is no database to copy yet.
    """
    if not os.path.exists(config.DB_PATH):
        logging.info("No database at %s yet — nothing to back up.",
                     config.DB_PATH)
        return None

    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(config.BACKUP_DIR, f"jobs_{timestamp}_{reason}.db")
    with closing(_connect()) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    logging.info("Database backed up to %s", path)
    _prune_backups()
    return path


# ======================================================
# PUBLIC API — SETUP
# ======================================================
def init_db() -> None:
    """
    Creates the jobs/meta tables if missing and adds missing columns.
    Backs the database up first whenever a migration would actually change it.
    """
    with closing(_connect()) as connection, connection:
        connection.execute(_SCHEMA)
        connection.execute(_META_SCHEMA)
        connection.execute(_JOB_SKILLS_SCHEMA)
        connection.execute(_EVENTS_SCHEMA)
        for statement in _INDEXES:
            connection.execute(statement)

    # Work out whether anything will be altered BEFORE altering it, so the
    # backup captures the pre-migration state.
    with closing(_connect()) as connection:
        columns = {row["name"] for row in
                   connection.execute("PRAGMA table_info(jobs)")}
        pending_columns = [column for column in _MIGRATED_COLUMNS
                           if column not in columns]
        legacy_keys = connection.execute(
            "SELECT COUNT(*) FROM jobs "
            "WHERE job_key LIKE 'id:%' OR job_key LIKE 'tc:%'"
        ).fetchone()[0]

    if pending_columns or legacy_keys:
        backup_database(reason="premigration")

    with closing(_connect()) as connection, connection:
        for column in pending_columns:
            connection.execute(
                f"ALTER TABLE jobs ADD COLUMN {column} "
                f"{_MIGRATED_COLUMNS[column]}")
            logging.info("Migrated database: added %s column.", column)
        # Rows from the single-site era have unprefixed keys ("id:123") —
        # prefix them with jobstreet: so they match the new multi-site keys.
        if legacy_keys:
            cursor = connection.execute(
                """UPDATE jobs SET job_key = 'jobstreet:' || job_key,
                                   source = 'jobstreet'
                   WHERE job_key LIKE 'id:%' OR job_key LIKE 'tc:%'""")
            logging.info("Migrated database: prefixed %d job keys with "
                         "'jobstreet:'.", cursor.rowcount)


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
               (job_key, title, company, location, url, source, salary,
                salary_min, salary_max, work_arrangement, listing_date,
                status, archived, search_keyword, score_percent,
                matched_skills, required_years, description,
                first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (row["job_key"], row["title"], row["company"], row["location"],
                 row["url"], row.get("source", ""), row.get("salary", ""),
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
# URL shapes for each site, mapped to their site-prefixed key format.
_JOB_URL_ID_PATTERNS = [
    ("jobstreet", re.compile(r"jobstreet\.com/job/(\d+)")),
    ("onlinejobs", re.compile(r"onlinejobs\.ph/jobseekers/job/.*-(\d+)/?(?:$|\?)")),
]


def _normalize_job_key(key_or_url: str) -> str:
    """Accepts a stored job_key OR a job URL from any site, returns the key."""
    for source, pattern in _JOB_URL_ID_PATTERNS:
        id_match = pattern.search(key_or_url)
        if id_match:
            return f"{source}:id:{id_match.group(1)}"
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


def update_statuses(status_by_key: dict[str, str]) -> int:
    """Bulk status update (used by the dashboard). Returns rows changed."""
    if not status_by_key:
        return 0
    with closing(_connect()) as connection, connection:
        cursor = connection.executemany(
            "UPDATE jobs SET status = ? WHERE job_key = ?",
            [(status, job_key) for job_key, status in status_by_key.items()],
        )
    logging.info("Updated status of %d jobs.", cursor.rowcount)
    return cursor.rowcount


# ======================================================
# PUBLIC API — APPLICATION LIFECYCLE
# ======================================================
def record_stage(key_or_url: str, stage: str, note: str | None = None) -> bool:
    """
    Moves a job to a new stage, appending to its history and updating the
    denormalised head on jobs. Refuses illegal transitions.
    Returns False when the job is unknown or the move is not allowed.
    """
    job_key = _normalize_job_key(key_or_url)
    now = _now()
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT status FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        if row is None:
            logging.error("No job with key '%s' in the database.", job_key)
            return False

        current = stages.parse(row["status"])
        target = stages.parse(stage)
        if not stages.can_move(current, target):
            allowed = ", ".join(stages.allowed_moves(current)) or "nothing"
            logging.error("Cannot move '%s' from %s to %s. Allowed: %s.",
                          job_key, current, target, allowed)
            return False

        connection.execute(
            "UPDATE jobs SET status = ?, status_changed_at = ? WHERE job_key = ?",
            (str(target), now, job_key))
        connection.execute(
            "INSERT INTO application_events (job_key, stage, note, occurred_at) "
            "VALUES (?, ?, ?, ?)", (job_key, str(target), note, now))
    logging.info("%s moved from %s to %s.", job_key, current, target)
    return True


def get_job(key_or_url: str) -> dict | None:
    """One stored job by job_key or by any of its site URLs."""
    job_key = _normalize_job_key(key_or_url)
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
    return dict(row) if row else None


def stage_history(job_key: str) -> list[dict]:
    """Every recorded stage change for a job, oldest first."""
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT stage, note, occurred_at FROM application_events "
            "WHERE job_key = ? ORDER BY occurred_at, id", (job_key,)).fetchall()
    return [dict(row) for row in rows]


def stalled_jobs() -> list[dict]:
    """
    Jobs awaiting an employer reply for longer than GHOSTED_AFTER_DAYS.
    Surfaced as a suggestion — nobody remembers to record a silence.
    """
    cutoff = (datetime.now() - timedelta(days=config.GHOSTED_AFTER_DAYS)
              ).strftime("%Y-%m-%d %H:%M:%S")
    awaiting = tuple(str(stage) for stage in stages.AWAITING_REPLY)
    placeholders = ",".join("?" for _ in awaiting)
    with closing(_connect()) as connection:
        rows = connection.execute(
            f"""SELECT job_key, title, company, status, status_changed_at
                FROM jobs
                WHERE archived = 0 AND status IN ({placeholders})
                  AND COALESCE(status_changed_at, first_seen) < ?
                ORDER BY status_changed_at""",
            (*awaiting, cutoff)).fetchall()
    return [dict(row) for row in rows]


def set_note(job_key: str, note: str) -> None:
    """Stores the free-text note shown on the job's detail panel."""
    with closing(_connect()) as connection, connection:
        connection.execute("UPDATE jobs SET notes = ? WHERE job_key = ?",
                           (note, job_key))


# ======================================================
# PUBLIC API — SKILL DEMAND
# ======================================================
def replace_job_skills(extracted: list[tuple[str, str, str, int]]) -> None:
    """
    Rewrites job_skills for the jobs represented in `extracted`.
    Deletes first so a rescore never leaves stale mentions behind.
    """
    if not extracted:
        return
    job_keys = sorted({row[0] for row in extracted})
    placeholders = ",".join("?" for _ in job_keys)
    with closing(_connect()) as connection, connection:
        connection.execute(
            f"DELETE FROM job_skills WHERE job_key IN ({placeholders})",
            job_keys)
        connection.executemany(
            "INSERT OR REPLACE INTO job_skills "
            "(job_key, skill, category, in_title) VALUES (?, ?, ?, ?)",
            extracted)
    logging.info("Stored %d skill mentions for %d jobs.",
                 len(extracted), len(job_keys))


def skill_demand(category: str | None = None, limit: int = 20) -> list[dict]:
    """
    How many active jobs mention each skill, most in demand first.
    Optionally restricted to one category (language, framework, ...).
    """
    query = ["""SELECT js.skill, js.category,
                       COUNT(DISTINCT js.job_key) AS demand,
                       SUM(js.in_title) AS in_title
                FROM job_skills js
                JOIN jobs j ON j.job_key = js.job_key
                WHERE j.archived = 0"""]
    params: list[object] = []
    if category:
        query.append("AND js.category = ?")
        params.append(category)
    query.append("GROUP BY js.skill, js.category ORDER BY demand DESC LIMIT ?")
    params.append(limit)
    with closing(_connect()) as connection:
        rows = connection.execute(" ".join(query), params).fetchall()
    return [dict(row) for row in rows]


def mark_duplicates(duplicates: dict[str, str]) -> int:
    """
    Records which listings duplicate which. Clears any previous marking first
    so a repost that turns out to be distinct is not left flagged forever.
    Returns the number of listings flagged.
    """
    with closing(_connect()) as connection, connection:
        connection.execute("UPDATE jobs SET duplicate_of = NULL "
                           "WHERE duplicate_of IS NOT NULL")
        if duplicates:
            connection.executemany(
                "UPDATE jobs SET duplicate_of = ? WHERE job_key = ?",
                [(keeper, duplicate)
                 for duplicate, keeper in duplicates.items()])
    if duplicates:
        logging.info("Flagged %d listing(s) as duplicates.", len(duplicates))
    return len(duplicates)


def total_active_jobs() -> int:
    """Denominator for demand percentages."""
    with closing(_connect()) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE archived = 0").fetchone()[0]


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


def fetch_all_jobs(include_archived: bool = False) -> list[dict]:
    """Returns every stored job (used by --rescore and the dashboard)."""
    query = "SELECT * FROM jobs"
    if not include_archived:
        query += " WHERE archived = 0"
    with closing(_connect()) as connection:
        rows = connection.execute(query).fetchall()
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

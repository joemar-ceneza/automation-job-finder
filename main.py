"""
main.py
Full pipeline: parse resume -> scrape JobStreet PH -> score & rank new jobs
-> persist to SQLite -> export ranked CSV + HTML report -> optional email
digest of new matches.

Usage:
    python main.py resume.pdf "python developer" --pages 2
    python main.py resume.pdf "python developer, automation engineer" --location "Metro Manila"
    python main.py resume.pdf "python developer" --full-desc --max-years 3 --min-score 15 --email
    python main.py --set-status https://ph.jobstreet.com/job/12345678 applied
"""
import argparse
import hashlib
import logging
import os
from dataclasses import asdict
from datetime import date, datetime

import config
import db_handler
import email_handler
import matcher
import resume_parser
import scraper


# ======================================================
# SETUP HELPERS
# ======================================================
def _setup_logging() -> None:
    """Creates required folders and configures console + file logging."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    os.makedirs(config.SCREENSHOTS_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume-to-JobStreet job matcher")
    parser.add_argument("resume_pdf", nargs="?", help="Path to your resume PDF")
    parser.add_argument("keyword", nargs="?",
                        help="Job title/keyword(s) to search, comma-separated, "
                             "e.g. 'python developer, automation engineer'")
    parser.add_argument("--skills", default=config.DEFAULT_SKILLS_FILE,
                        help="Path to skills keyword list")
    parser.add_argument("--pages", type=int, default=config.DEFAULT_PAGES,
                        help="Number of search-result pages to scrape per keyword")
    parser.add_argument("--delay", type=float, default=config.DEFAULT_DELAY_SECONDS,
                        help="Seconds between page requests")
    parser.add_argument("--location", default="",
                        help="Limit results to a location, e.g. 'Metro Manila'")
    parser.add_argument("--debug", action="store_true",
                        help="Run browser visibly, save page HTML for every page")
    parser.add_argument("--full-desc", action="store_true",
                        help="Visit each job's detail page for the full description (slower)")
    parser.add_argument("--max-years", type=int, default=None,
                        help="Your years of experience — jobs requiring more are filtered out")
    parser.add_argument("--min-score", type=float, default=None,
                        help="Exclude jobs scoring below this percentage from exports")
    parser.add_argument("--min-salary", type=int, default=None,
                        help="Exclude jobs whose stated max monthly salary (PHP) is below this "
                             "(jobs without a stated salary are kept)")
    parser.add_argument("--only-new", action="store_true",
                        help="Export only jobs not seen in previous runs to the CSV")
    parser.add_argument("--rescore", action="store_true",
                        help="Re-score all stored jobs against the current skill list")
    parser.add_argument("--prune-days", type=int, default=None,
                        help="Archive jobs not seen in this many days (excluded from exports)")
    parser.add_argument("--set-status", nargs=2, metavar=("JOB", "STATUS"),
                        help="Record a job's status (interested/applied/rejected/...) "
                             "by job_key or URL, then exit")
    parser.add_argument("--email", action="store_true",
                        help="Email a digest of new matches via Gmail SMTP (see .env.example)")
    parser.add_argument("--out", default=config.DEFAULT_OUTPUT_CSV,
                        help="Output CSV path")
    parser.add_argument("--html", default=config.DEFAULT_OUTPUT_HTML,
                        help="Output HTML report path")
    args = parser.parse_args()

    maintenance_only = args.set_status or (args.prune_days is not None
                                           and not args.keyword)
    if not maintenance_only and (not args.resume_pdf or not args.keyword):
        parser.error("resume_pdf and keyword are required "
                     "(unless using --set-status or --prune-days alone)")
    return args


def _log_step(number: int, message: str) -> None:
    logging.info("=" * 70)
    logging.info("STEP %d — %s", number, message)
    logging.info("=" * 70)


def _skills_hash(resume_skills: list[str]) -> str:
    """Fingerprint of the matched skill list, to detect skills.txt changes."""
    joined = "\n".join(sorted(skill.lower() for skill in resume_skills))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _apply_export_filters(rows: list[dict], min_score: float | None,
                          min_salary: int | None) -> list[dict]:
    """Drops rows below --min-score / --min-salary (unstated salaries kept)."""
    kept = rows
    if min_score is not None:
        kept = [row for row in kept if (row["score_percent"] or 0) >= min_score]
    if min_salary is not None:
        kept = [row for row in kept
                if not row.get("salary_max") or row["salary_max"] >= min_salary]
    dropped = len(rows) - len(kept)
    if dropped:
        logging.info("Export filters removed %d rows (--min-score/--min-salary).",
                     dropped)
    return kept


# ======================================================
# MAINTENANCE MODE (no scraping)
# ======================================================
def _run_maintenance(args: argparse.Namespace) -> None:
    """Handles --set-status / standalone --prune-days, then returns."""
    db_handler.init_db()
    if args.set_status:
        job, status = args.set_status
        db_handler.set_status(job, status)
    if args.prune_days is not None:
        db_handler.prune_stale(args.prune_days)


# ======================================================
# ORCHESTRATOR
# ======================================================
def main() -> None:
    args = _parse_args()
    _setup_logging()

    if args.set_status or (args.prune_days is not None and not args.keyword):
        _run_maintenance(args)
        return

    # Step 1: Parse resume and match skills
    _log_step(1, "Parsing resume and matching skills")
    resume_text = resume_parser.extract_text_from_pdf(args.resume_pdf)
    all_skills = resume_parser.load_skills(args.skills)
    resume_skills = resume_parser.find_matching_skills(resume_text, all_skills)
    logging.info("Found %d/%d skills in resume: %s",
                 len(resume_skills), len(all_skills), ", ".join(resume_skills))
    if not resume_skills:
        logging.warning("No skills matched. Edit %s to include terms from "
                        "your resume. Exiting.", args.skills)
        return

    # Step 2: Scrape JobStreet PH
    keywords = [keyword.strip() for keyword in args.keyword.split(",")
                if keyword.strip()]
    _log_step(2, f"Scraping JobStreet PH for {len(keywords)} keyword(s)")
    jobs = scraper.run_scraper(keywords, max_pages=args.pages,
                               delay_seconds=args.delay, debug=args.debug,
                               fetch_details=args.full_desc,
                               location=args.location)
    if not jobs:
        logging.error("No jobs scraped. Inspect the debug HTML saved in %s "
                      "and update SELECTORS in config.py if JobStreet changed "
                      "its markup.", config.LOGS_DIR)
        return

    # Step 3: Database housekeeping (prune, rescore, split new vs seen)
    _log_step(3, "Checking database for already-seen jobs")
    db_handler.init_db()
    if args.prune_days is not None:
        db_handler.prune_stale(args.prune_days)

    current_hash = _skills_hash(resume_skills)
    stored_hash = db_handler.get_meta("skills_hash")
    if args.rescore:
        rescored = matcher.rank_jobs(db_handler.fetch_all_active(), resume_skills)
        db_handler.update_scores(rescored)
    elif stored_hash and stored_hash != current_hash:
        logging.warning("Your matched skill list changed since jobs were last "
                        "scored — stored scores may be stale. Run with "
                        "--rescore to refresh them.")
    db_handler.set_meta("skills_hash", current_hash)

    seen_keys = db_handler.get_existing_keys([job.job_key for job in jobs])
    new_jobs = [job for job in jobs if job.job_key not in seen_keys]
    logging.info("%d unique jobs scraped: %d new, %d already seen",
                 len(jobs), len(new_jobs), len(seen_keys))

    # Step 4: Score new jobs against resume skills
    _log_step(4, f"Scoring {len(new_jobs)} new jobs")
    new_rows = matcher.rank_jobs([asdict(job) for job in new_jobs],
                                 resume_skills,
                                 max_experience_years=args.max_years)

    # Step 5: Persist results to SQLite
    _log_step(5, "Saving results to database")
    descriptions = {job.job_key: (job.description or job.teaser) for job in new_jobs}
    for row in new_rows:
        row["description"] = descriptions.get(row["job_key"], "")
    db_handler.insert_jobs(new_rows)
    db_handler.mark_seen(list(seen_keys))

    # Step 6: Export ranked CSV + HTML report
    _log_step(6, "Exporting ranked CSV and HTML report")
    today = date.today().isoformat()
    for row in new_rows:
        row["first_seen"] = today
        row["new_this_run"] = "yes"
    if args.only_new:
        seen_rows = []
        logging.info("--only-new: exporting only the %d new jobs.", len(new_rows))
    else:
        seen_rows = db_handler.fetch_jobs(list(seen_keys))
    for row in seen_rows:
        row["first_seen"] = (row["first_seen"] or "")[:10]  # date only, match new rows
        row["new_this_run"] = "no"
    combined = sorted(new_rows + seen_rows,
                      key=lambda row: row["score_percent"] or 0, reverse=True)
    combined = _apply_export_filters(combined, args.min_score, args.min_salary)
    matcher.write_csv(combined, args.out)
    generated_note = (f"Generated {datetime.now():%Y-%m-%d %H:%M} — "
                      f"keywords: {', '.join(keywords)}"
                      + (f" — location: {args.location}" if args.location else ""))
    matcher.write_html_report(combined, args.html, generated_note)

    # Step 7: Email digest of new matches (optional)
    if args.email:
        _log_step(7, "Sending email digest")
        digest_rows = [row for row in combined if row["new_this_run"] == "yes"]
        email_handler.run_email_digest(digest_rows)

    logging.info("Done. Top matches:")
    for row in combined[:10]:
        logging.info("  %s%% - %s @ %s (%s)", row["score_percent"], row["title"],
                     row["company"], "NEW" if row["new_this_run"] == "yes" else "seen")
    logging.info("Ranked CSV: %s | HTML report: %s | Database: %s",
                 args.out, args.html, config.DB_PATH)


if __name__ == "__main__":
    main()

"""
main.py
Full pipeline: parse resume -> scrape JobStreet PH -> score & rank new jobs
-> persist to SQLite -> export ranked CSV.

Usage:
    python main.py resume.pdf "python developer" --skills skills.txt --pages 2
    python main.py resume.pdf "python developer" --full-desc --max-years 3
"""
import argparse
import logging
import os
from dataclasses import asdict
from datetime import date

import config
import db_handler
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
    parser.add_argument("resume_pdf", help="Path to your resume PDF")
    parser.add_argument("keyword", help="Job title/keyword to search, e.g. 'python developer'")
    parser.add_argument("--skills", default=config.DEFAULT_SKILLS_FILE,
                        help="Path to skills keyword list")
    parser.add_argument("--pages", type=int, default=config.DEFAULT_PAGES,
                        help="Number of search-result pages to scrape")
    parser.add_argument("--delay", type=float, default=config.DEFAULT_DELAY_SECONDS,
                        help="Seconds between page requests")
    parser.add_argument("--debug", action="store_true",
                        help="Run browser visibly, save page HTML for every page")
    parser.add_argument("--full-desc", action="store_true",
                        help="Visit each job's detail page for the full description (slower)")
    parser.add_argument("--max-years", type=int, default=None,
                        help="Your years of experience — jobs requiring more are filtered out")
    parser.add_argument("--only-new", action="store_true",
                        help="Export only jobs not seen in previous runs to the CSV")
    parser.add_argument("--out", default=config.DEFAULT_OUTPUT_CSV,
                        help="Output CSV path")
    return parser.parse_args()


def _log_step(number: int, message: str) -> None:
    logging.info("=" * 70)
    logging.info("STEP %d — %s", number, message)
    logging.info("=" * 70)


# ======================================================
# ORCHESTRATOR
# ======================================================
def main() -> None:
    args = _parse_args()
    _setup_logging()

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
    _log_step(2, f"Scraping JobStreet PH for '{args.keyword}'")
    jobs = scraper.run_scraper(args.keyword, max_pages=args.pages,
                               delay_seconds=args.delay, debug=args.debug,
                               fetch_details=args.full_desc)
    if not jobs:
        logging.error("No jobs scraped. Inspect the debug HTML saved in %s "
                      "and update SELECTORS in config.py if JobStreet changed "
                      "its markup.", config.LOGS_DIR)
        return

    # Step 3: Split new vs already-seen jobs via the database
    _log_step(3, "Checking database for already-seen jobs")
    db_handler.init_db()
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
    db_handler.insert_jobs(new_rows, args.keyword)
    db_handler.mark_seen(list(seen_keys))

    # Step 6: Export ranked CSV (new jobs + stored scores for seen jobs)
    _log_step(6, "Exporting ranked CSV")
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
    matcher.write_csv(combined, args.out)

    logging.info("Done. Top matches:")
    for row in combined[:10]:
        logging.info("  %s%% - %s @ %s (%s)", row["score_percent"], row["title"],
                     row["company"], "NEW" if row["new_this_run"] == "yes" else "seen")
    logging.info("Ranked CSV: %s | Database: %s", args.out, config.DB_PATH)


if __name__ == "__main__":
    main()

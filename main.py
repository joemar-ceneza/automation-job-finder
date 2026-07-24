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
from logging.handlers import RotatingFileHandler

import ai_explain
import config
import cover_letter
import db_handler
import dedupe
import documents
import email_handler
import llm
import matcher
import optimizer
import resume_model
import resume_import
import resume_parser
import resumes
import scraper_common
import scraper_jobstreet
import scraper_onlinejobs
import skill_extractor
import stages

# Site name -> scraper module. Each module exposes run_scraper().
SITE_SCRAPERS = {
    "jobstreet": scraper_jobstreet,
    "onlinejobs": scraper_onlinejobs,
}


# ======================================================
# SETUP HELPERS
# ======================================================
def _setup_logging() -> None:
    """Creates required folders and configures console + rotating file logging."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    os.makedirs(config.SCREENSHOTS_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(config.LOG_FILE, encoding="utf-8",
                                maxBytes=config.LOG_MAX_BYTES,
                                backupCount=config.LOG_BACKUP_COUNT),
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
    parser.add_argument("--site", default=",".join(config.DEFAULT_SITES),
                        help="Comma-separated sites to search: "
                             + ", ".join(SITE_SCRAPERS))
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
    parser.add_argument("--set-status", nargs=2, metavar=("JOB", "STAGE"),
                        help="Move a job to a stage by job_key or URL, then "
                             "exit. Stages: " + ", ".join(stages.BOARD_ORDER))
    parser.add_argument("--note", default=None,
                        help="Note to attach to a --set-status change")
    parser.add_argument("--stalled", action="store_true",
                        help="List applications with no reply in "
                             f"{config.GHOSTED_AFTER_DAYS}+ days, then exit")
    parser.add_argument("--generate-skills", action="store_true",
                        help="Draft a skill list from your resume PDF into "
                             "skills_draft.txt (review it, then replace "
                             "skills.txt), then exit")
    parser.add_argument("--import-resume", action="store_true",
                        help="Convert your resume PDF into an editable master "
                             "resume at master_resume.md, then exit")
    parser.add_argument("--tailor", metavar="JOB",
                        help="Tailor your master resume to a job (job_key or "
                             "URL) and export it, then exit")
    parser.add_argument("--cover-letter", metavar="JOB",
                        help="Draft a cover letter for a job (job_key or URL) "
                             "and export it, then exit")
    parser.add_argument("--explain", metavar="JOB",
                        help="Explain why a job scored what it did, then exit")
    parser.add_argument("--ai", action="store_true",
                        help="Use the configured AI provider to enrich "
                             "--explain (falls back to Standard if unavailable)")
    parser.add_argument("--ai-usage", action="store_true",
                        help="Show total AI token usage, then exit")
    parser.add_argument("--compare", metavar="JOB",
                        help="Rank every resume against a job, then exit")
    parser.add_argument("--resume", metavar="NAME", default=None,
                        help="Which resume to use (default: the one set by "
                             "--set-default-resume)")
    parser.add_argument("--list-resumes", action="store_true",
                        help="List the resumes you maintain, then exit")
    parser.add_argument("--set-default-resume", metavar="NAME",
                        help="Choose the resume used when --resume is omitted")
    parser.add_argument("--tone", default=config.COVER_LETTER_TONE,
                        help="Cover letter tone: "
                             + ", ".join(cover_letter.available_tones()))
    parser.add_argument("--recipient", default=None,
                        help="Name the letter is addressed to "
                             f"(default: {config.COVER_LETTER_RECIPIENT})")
    parser.add_argument("--formats", default="md,docx,pdf",
                        help="Formats for --tailor: " +
                             ", ".join(config.DOCUMENT_FORMATS))
    parser.add_argument("--backup", action="store_true",
                        help="Copy the database to output/backups/, then exit")
    parser.add_argument("--calibrate", action="store_true",
                        help="Suggest a TARGET_MATCH_SKILLS value from your "
                             "stored jobs, then exit")
    parser.add_argument("--email", action="store_true",
                        help="Email a digest of new matches via Gmail SMTP (see .env.example)")
    parser.add_argument("--out", default=config.DEFAULT_OUTPUT_CSV,
                        help="Output CSV path")
    parser.add_argument("--html", default=config.DEFAULT_OUTPUT_HTML,
                        help="Output HTML report path")
    args = parser.parse_args()

    if args.generate_skills and not args.resume_pdf:
        parser.error("--generate-skills needs your resume PDF, e.g. "
                     "python main.py resume.pdf --generate-skills")
    if args.import_resume and not args.resume_pdf:
        parser.error("--import-resume needs your resume PDF, e.g. "
                     "python main.py resume.pdf --import-resume")
    maintenance_only = (args.set_status or args.generate_skills
                        or args.backup or args.calibrate or args.stalled
                        or args.import_resume or args.tailor
                        or args.cover_letter or args.compare or args.explain
                        or args.ai_usage or args.list_resumes
                        or args.set_default_resume
                        or (args.prune_days is not None and not args.keyword))
    if not maintenance_only and (not args.resume_pdf or not args.keyword):
        parser.error("resume_pdf and keyword are required (unless using "
                     "--set-status, --generate-skills, or --prune-days alone)")

    args.sites = [site.strip().lower() for site in args.site.split(",")
                  if site.strip()]
    unknown_sites = [site for site in args.sites if site not in SITE_SCRAPERS]
    if unknown_sites:
        parser.error(f"unknown site(s): {', '.join(unknown_sites)} — "
                     f"choose from: {', '.join(SITE_SCRAPERS)}")
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
    """Handles --set-status / --stalled / standalone --prune-days."""
    db_handler.init_db()
    if args.set_status:
        job, stage = args.set_status
        db_handler.record_stage(job, stage, note=args.note)
    if args.stalled:
        _report_stalled()
    if args.prune_days is not None:
        db_handler.prune_stale(args.prune_days)


def _report_stalled() -> None:
    """Lists applications the employer has gone quiet on."""
    waiting = db_handler.stalled_jobs()
    if not waiting:
        logging.info("Nothing has been waiting longer than %d days.",
                     config.GHOSTED_AFTER_DAYS)
        return
    logging.info("%d application(s) with no reply in %d+ days — mark them "
                 "ghosted if you have given up:", len(waiting),
                 config.GHOSTED_AFTER_DAYS)
    for job in waiting:
        logging.info("  [%s] %s @ %s (since %s)", job["status"], job["title"],
                     job["company"] or "unknown", job["status_changed_at"])


def _run_import_resume(args: argparse.Namespace) -> None:
    """Bootstraps a resume in resumes/ from a PDF."""
    db_handler.init_db()
    name = args.resume or config.DEFAULT_RESUME_NAME
    existing = resumes.get(name)
    if existing is not None:
        logging.error("A resume called '%s' already exists at %s — refusing "
                      "to overwrite your edits. Import under another name "
                      "with --resume <name>.", name, existing.path)
        return

    text = resume_parser.extract_text_from_pdf(args.resume_pdf)
    resume = resume_import.from_resume_text(text)
    if not resume.sections:
        logging.warning("No sections recognised in %s — is the PDF text-based "
                        "rather than a scanned image?", args.resume_pdf)
        return

    path = os.path.join(config.RESUMES_DIR, f"{name}.md")
    resume_import.write_draft(resume, path)
    logging.info("Sections found: %s",
                 ", ".join(section.name for section in resume.sections))
    logging.info("Review %s and correct anything the import got wrong. From "
                 "now on it is the source of truth, not the PDF.", path)


def _load_job_and_resume(job_reference: str, resume_name: str | None = None):
    """
    Shared setup for the document commands. Returns (job, resume), or
    (None, None) after explaining what is missing.
    """
    db_handler.init_db()
    job = db_handler.get_job(job_reference)
    if job is None:
        logging.error("No job matching '%s' in the database.", job_reference)
        return None, None
    reference = resumes.resolve(resume_name)
    if reference is None:
        return None, None
    logging.info("Using resume '%s'.", reference.name)
    return job, reference.load()


def _run_list_resumes() -> None:
    """Shows the resumes on disk and which one is the default."""
    db_handler.init_db()
    references = resumes.available()
    if not references:
        logging.warning("No resumes in %s yet. Create one with: "
                        "python main.py <resume.pdf> --import-resume",
                        config.RESUMES_DIR)
        return
    default = resumes.default_name()
    logging.info("%d resume(s) in %s:", len(references), config.RESUMES_DIR)
    for reference in references:
        resume = reference.load()
        marker = "*" if reference.name == default else " "
        logging.info("  %s %-14s %2d sections, %2d skills, %2d bullets",
                     marker, reference.name, len(resume.sections),
                     len(resume.listed_skills()), len(resume.all_bullets()))
    logging.info("  (* = used when --resume is omitted)")


def _run_explain(args: argparse.Namespace) -> None:
    """Explains one job's score, optionally with an AI narrative."""
    job, resume = _load_job_and_resume(args.explain, args.resume)
    if job is None:
        return
    resume_skills = resume_parser.find_matching_skills(
        resume.full_text(), resume_parser.load_skills(args.skills))

    provider = llm.get_provider(db_handler) if args.ai else llm.NullProvider()
    result = ai_explain.enrich(job, resume_skills, resume.full_text(),
                               provider, effort=config.AI_EFFORT)

    logging.info("=" * 70)
    logging.info("%s @ %s — %.1f%%", job["title"], job.get("company") or "?",
                 result.base.score_percent)
    logging.info("=" * 70)
    for line in result.base.lines:
        logging.info("  %s", line)

    if not result.ai_used:
        if args.ai:
            logging.info("")
            logging.info("AI narrative unavailable — showing the deterministic "
                         "explanation only. Configure a provider in .env to "
                         "enable it (see .env.example).")
        return

    logging.info("")
    logging.info("AI narrative (%s%s):", result.model,
                 ", cached" if result.from_cache else "")
    logging.info("  %s", result.summary)
    if result.strengths:
        logging.info("  Strengths: %s", "; ".join(result.strengths))
    if result.weaknesses:
        logging.info("  Weak areas: %s", "; ".join(result.weaknesses))
    if result.improvements:
        logging.info("  Do next: %s", "; ".join(result.improvements))
    if result.advice:
        logging.info("  %s", result.advice)


def _run_ai_usage() -> None:
    """Reports total AI token spend."""
    db_handler.init_db()
    usage = db_handler.ai_usage()
    logging.info("AI calls cached: %d", usage["calls"])
    logging.info("Input tokens:  %d", usage["input_tokens"])
    logging.info("Output tokens: %d", usage["output_tokens"])
    logging.info("(Cost depends on your provider; local models are free.)")


def _run_compare(args: argparse.Namespace) -> None:
    """Ranks every resume against one job."""
    db_handler.init_db()
    job = db_handler.get_job(args.compare)
    if job is None:
        logging.error("No job matching '%s' in the database.", args.compare)
        return
    references = resumes.available()
    if len(references) < 2:
        logging.warning("Only %d resume(s) found — add another to %s to have "
                        "something to compare.", len(references),
                        config.RESUMES_DIR)
        if not references:
            return

    rankings = optimizer.compare(
        job, [(ref.name, ref.load()) for ref in references])

    logging.info("=" * 70)
    logging.info("%s @ %s", job["title"], job.get("company") or "unknown")
    logging.info("=" * 70)
    logging.info("  %-14s %7s %7s %7s   %s", "resume", "overall", "match",
                 "ats", "missing")
    for index, ranking in enumerate(rankings):
        logging.info("  %-14s %6.1f%% %6.1f%% %6.1f    %s",
                     ("→ " if index == 0 else "  ") + ranking.name,
                     ranking.combined, ranking.match_percent,
                     ranking.ats_score,
                     ", ".join(ranking.missing[:4]) or "nothing")

    best = rankings[0]
    tied = [ranking.name for ranking in rankings
            if ranking.combined == best.combined]
    logging.info("")
    if len(tied) > 1:
        # Recommending one of several identical resumes would be arbitrary,
        # and the tie itself is the useful information: nothing that
        # distinguishes them is asked for here.
        logging.info("%s score identically — nothing that separates them is "
                     "asked for in this advert, so use whichever you prefer.",
                     " and ".join(f"'{name}'" for name in tied))
    else:
        logging.info("Use '%s' for this job — it evidences %d of the %d "
                     "skills the advert names, %.1f points ahead of '%s'.",
                     best.name, len(best.matched),
                     len(best.matched) + len(best.missing),
                     best.combined - rankings[1].combined, rankings[1].name)
    if best.unmentioned:
        logging.info("Before sending, add to the Skills section: %s",
                     ", ".join(best.unmentioned))


def _export_all(document, stem: str, formats: str, writer) -> None:
    """Writes one document in each requested format."""
    for fmt in [part.strip().lower() for part in formats.split(",")
                if part.strip()]:
        if fmt not in config.DOCUMENT_FORMATS:
            logging.warning("Skipping unknown format '%s'.", fmt)
            continue
        writer(document, os.path.join(config.DOCUMENTS_DIR,
                                      f"{stem}.{fmt}"), fmt)


def _run_cover_letter(args: argparse.Namespace) -> None:
    """Drafts a cover letter for one job and exports it."""
    job, resume = _load_job_and_resume(args.cover_letter, args.resume)
    if job is None:
        return
    if args.tone not in cover_letter.available_tones():
        logging.error("Unknown tone '%s'. Available: %s", args.tone,
                      ", ".join(cover_letter.available_tones()))
        return

    letter = cover_letter.compose(resume, job, tone=args.tone,
                                  recipient=args.recipient)
    logging.info("=" * 70)
    for line in letter.to_text().splitlines():
        logging.info("  %s", line)
    logging.info("=" * 70)
    logging.info("Read it before sending — a template letter reads like one, "
                 "and the opening line is usually worth rewriting yourself.")

    stem = documents.slugify(
        f"cover-letter-{job['title']}-{job.get('company') or ''}")
    _export_all(letter, stem, args.formats, documents.write_letter)


def _run_tailor(args: argparse.Namespace) -> None:
    """Tailors the master resume to one job and exports it."""
    job, resume = _load_job_and_resume(args.tailor, args.resume)
    if job is None:
        return

    result = optimizer.optimise(resume, job)

    logging.info("=" * 70)
    logging.info("%s @ %s", job["title"], job.get("company") or "unknown")
    logging.info("ATS score: %.1f / 100", result.ats_score)
    logging.info("=" * 70)
    for check in result.checks:
        marker = "ok  " if check.passed else "    "
        logging.info("  %s %-24s %5.1f/%-4.0f %s", marker, check.name,
                     check.points, check.max_points, check.detail)
    logging.info("")
    for change in result.changes:
        logging.info("  - %s", change)

    stem = documents.slugify(f"{job['title']}-{job.get('company') or ''}")
    _export_all(result.resume, stem, args.formats, documents.write)


def _run_calibrate() -> None:
    """Suggests a TARGET_MATCH_SKILLS value from the stored corpus."""
    db_handler.init_db()
    result = matcher.suggest_target_match(db_handler.fetch_all_jobs())
    if not result["table"]:
        logging.warning(
            "Only %d stored jobs — need at least %d before a suggestion means "
            "anything. Scrape more, then run --calibrate again.",
            result["sample"], config.CALIBRATION_MIN_JOBS)
        return

    share = (result["with_full_text"] / result["sample"] * 100
             if result["sample"] else 0)
    logging.info("Scale options across %d stored jobs.", result["sample"])
    logging.info("  %d of them (%.0f%%) were scored on a full description "
                 "rather than a search-card teaser.",
                 result["with_full_text"], share)
    logging.info("  The median job matches %d of your skills.",
                 result["median_matches"])
    if share < 50:
        logging.warning("  Most of this corpus is teaser-only, so the "
                        "suggestion below will not hold once you run with "
                        "--full-desc. Consider doing that first.")
    logging.info("  %-3s %8s %8s %8s", "K", "median", "p90", "max")
    for target, median, p90, top in result["table"]:
        marker = "  <-- suggested" if target == result["suggested"] else ""
        logging.info("  %-3d %8.1f %8.1f %8.1f%s", target, median, p90, top,
                     marker)

    if result["suggested"] is None:
        logging.warning("No value put the median in 30-50 with a sensible top "
                        "end — pick from the table by eye.")
        return
    logging.info("Set TARGET_MATCH_SKILLS = %d in config.py (currently %d), "
                 "then run --rescore.", result["suggested"],
                 config.TARGET_MATCH_SKILLS)


def _run_generate_skills(args: argparse.Namespace) -> None:
    """Drafts a skill list from the resume PDF into skills_draft.txt."""
    resume_text = resume_parser.extract_text_from_pdf(args.resume_pdf)
    hits, extras = resume_parser.generate_skills_draft(resume_text)
    if not hits and not extras:
        logging.warning("No skills detected in %s — is the PDF text-based "
                        "(not a scanned image)?", args.resume_pdf)
        return
    resume_parser.write_skills_draft(hits, extras, config.DEFAULT_SKILLS_DRAFT)
    logging.info("Review %s, remove anything that doesn't reflect your "
                 "skills, then replace %s with it.",
                 config.DEFAULT_SKILLS_DRAFT, config.DEFAULT_SKILLS_FILE)


# ======================================================
# ORCHESTRATOR
# ======================================================
def main() -> None:
    args = _parse_args()
    _setup_logging()

    if args.generate_skills:
        _run_generate_skills(args)
        return
    if args.import_resume:
        _run_import_resume(args)
        return
    if args.tailor:
        _run_tailor(args)
        return
    if args.cover_letter:
        _run_cover_letter(args)
        return
    if args.list_resumes:
        _run_list_resumes()
        return
    if args.set_default_resume:
        db_handler.init_db()
        resumes.set_default(args.set_default_resume)
        return
    if args.explain:
        _run_explain(args)
        return
    if args.ai_usage:
        _run_ai_usage()
        return
    if args.compare:
        _run_compare(args)
        return
    if args.backup:
        db_handler.backup_database(reason="manual")
        return
    if args.calibrate:
        _run_calibrate()
        return
    if (args.set_status or args.stalled
            or (args.prune_days is not None and not args.keyword)):
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

    # Step 2: Scrape the selected job sites
    keywords = [keyword.strip() for keyword in args.keyword.split(",")
                if keyword.strip()]
    _log_step(2, f"Scraping {len(args.sites)} site(s) for "
                 f"{len(keywords)} keyword(s)")
    jobs = []
    for site in args.sites:
        try:
            site_jobs = SITE_SCRAPERS[site].run_scraper(
                keywords, max_pages=args.pages, delay_seconds=args.delay,
                debug=args.debug, fetch_details=args.full_desc,
                location=args.location)
            logging.info("[%s] Collected %d unique listings.", site, len(site_jobs))
            jobs.extend(site_jobs)
        except Exception as e:
            logging.error("[%s] Scraper failed, continuing with other "
                          "sites: %s", site, e)
    jobs = scraper_common.filter_blocklisted(jobs)
    if not jobs:
        logging.error("No jobs scraped from any site. Inspect the debug HTML "
                      "saved in %s and update SELECTORS in config.py if a "
                      "site changed its markup.", config.LOGS_DIR)
        return

    # Step 3: Database housekeeping (prune, rescore, split new vs seen)
    _log_step(3, "Checking database for already-seen jobs")
    db_handler.init_db()
    if args.prune_days is not None:
        db_handler.prune_stale(args.prune_days)

    current_hash = _skills_hash(resume_skills)
    stored_hash = db_handler.get_meta("skills_hash")
    stored_scale = db_handler.get_meta("score_scale")
    current_scale = str(config.SCORE_SCALE_VERSION)
    if args.rescore:
        stored = db_handler.fetch_all_jobs()
        rescored = matcher.rank_jobs(stored, resume_skills)
        db_handler.update_scores(rescored)
        db_handler.replace_job_skills(skill_extractor.extract_for_rows(stored))
        db_handler.set_meta("score_scale", current_scale)
    elif stored_scale and stored_scale != current_scale:
        logging.warning("Stored scores use scale v%s but this build scores on "
                        "v%s — the two are NOT comparable. Run with --rescore "
                        "to restate them.", stored_scale, current_scale)
    elif stored_hash and stored_hash != current_hash:
        logging.warning("Your matched skill list changed since jobs were last "
                        "scored — stored scores may be stale. Run with "
                        "--rescore to refresh them.")
    db_handler.set_meta("skills_hash", current_hash)
    if not stored_scale:
        db_handler.set_meta("score_scale", current_scale)

    seen_keys = db_handler.get_existing_keys([job.job_key for job in jobs])
    new_jobs = [job for job in jobs if job.job_key not in seen_keys]
    logging.info("%d unique jobs scraped: %d new, %d already seen",
                 len(jobs), len(new_jobs), len(seen_keys))

    # Step 4: Score new jobs against resume skills
    _log_step(4, f"Scoring {len(new_jobs)} new jobs")
    new_rows = matcher.rank_jobs([asdict(job) for job in new_jobs],
                                 resume_skills,
                                 max_experience_years=args.max_years)

    # Step 5: Persist results and extract skill demand
    _log_step(5, "Saving results to database")
    descriptions = {job.job_key: (job.description or job.teaser) for job in new_jobs}
    for row in new_rows:
        row["description"] = descriptions.get(row["job_key"], "")
    db_handler.insert_jobs(new_rows)
    db_handler.mark_seen(list(seen_keys))
    db_handler.replace_job_skills(skill_extractor.extract_for_rows(new_rows))

    # A job seen before may now carry a full description (--full-desc), and
    # its stored score was computed from the teaser alone. Re-score only those.
    enriched = db_handler.update_descriptions(
        [asdict(job) for job in jobs if job.job_key in seen_keys])
    if enriched:
        refreshed = db_handler.fetch_jobs(enriched)
        db_handler.update_scores(matcher.rank_jobs(refreshed, resume_skills))
        db_handler.replace_job_skills(
            skill_extractor.extract_for_rows(refreshed))
        logging.info("Re-scored %d job(s) against their fuller description.",
                     len(enriched))

    db_handler.mark_duplicates(dedupe.find_duplicates(db_handler.fetch_all_jobs()))

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
                      f"sites: {', '.join(args.sites)} — "
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

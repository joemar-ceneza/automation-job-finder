"""
matcher.py
Scores scraped job listings against a resume's matched skills using weighted
keyword matching (skills in the job title count more than skills in the
teaser/description), extracts each job's required years of experience, and
writes a ranked CSV.
"""
import argparse
import csv
import json
import logging
import os
import re

import config
from resume_parser import skill_in_text

CSV_FIELDNAMES = [
    "score_percent", "title", "company", "location", "salary",
    "matched_skills", "required_years", "first_seen", "new_this_run", "url",
]


# ======================================================
# SKILL MATCHING
# ======================================================
def _score_job(title: str, body: str,
               resume_skills: list[str]) -> tuple[float, list[str], list[str]]:
    """
    Weighted keyword score: a skill found in the job title counts
    TITLE_MATCH_WEIGHT, a skill found only in the body counts
    BODY_MATCH_WEIGHT. The percentage is normalized against the maximum
    possible (every skill appearing in the title).
    Returns (score_percent, title_matches, body_matches).
    """
    if not resume_skills:
        return 0.0, [], []

    title_lower = title.lower()
    body_lower = body.lower()
    title_matches = []
    body_matches = []
    for skill in resume_skills:
        if skill_in_text(skill, title_lower):
            title_matches.append(skill)
        elif skill_in_text(skill, body_lower):
            body_matches.append(skill)

    weighted = (len(title_matches) * config.TITLE_MATCH_WEIGHT
                + len(body_matches) * config.BODY_MATCH_WEIGHT)
    max_weighted = len(resume_skills) * config.TITLE_MATCH_WEIGHT
    return round(weighted / max_weighted * 100, 1), title_matches, body_matches


# ======================================================
# EXPERIENCE EXTRACTION
# ======================================================
_YEARS_PATTERNS = [
    # "at least 5 years", "minimum of 3 years", "min. 2 yrs"
    re.compile(r"(?:at least|minimum(?: of)?|min\.?)\s+(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE),
    # "3-5 years", "3 to 5 yrs" — take the lower bound
    re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*\d{1,2}\s*(?:years?|yrs?)", re.IGNORECASE),
    # "5+ years ... experience" within the same clause
    re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)[^.\n]{0,40}\bexperience", re.IGNORECASE),
]


def _extract_required_years(job_text: str) -> int | None:
    """
    Extracts the minimum years of experience a job asks for.
    Returns None when no requirement is stated.
    """
    candidates = []
    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(job_text):
            years = int(match.group(1))
            if 0 < years <= config.MAX_PLAUSIBLE_YEARS:
                candidates.append(years)
    return min(candidates) if candidates else None


# ======================================================
# PUBLIC API
# ======================================================
def rank_jobs(jobs: list[dict], resume_skills: list[str],
              max_experience_years: int | None = None) -> list[dict]:
    """
    Scores each job dict (expects title/teaser/description/company/location/
    url/job_key keys) against the resume skills, drops jobs that require more
    experience than max_experience_years (when given), and returns rows
    sorted by score descending.
    """
    ranked = []
    filtered_out = 0
    for job in jobs:
        title = job.get("title", "")
        body_parts = (job.get("teaser", ""), job.get("description", ""))
        body = " ".join(part for part in body_parts if part)

        score, title_matches, body_matches = _score_job(title, body, resume_skills)
        required_years = _extract_required_years(f"{title} {body}")

        if (max_experience_years is not None and required_years is not None
                and required_years > max_experience_years):
            filtered_out += 1
            logging.debug("Filtered out '%s' — requires %d years of experience",
                          title, required_years)
            continue

        matched = [f"{skill} (title)" for skill in title_matches] + body_matches
        ranked.append({
            "job_key": job.get("job_key", ""),
            "score_percent": score,
            "title": title,
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "salary": job.get("salary", ""),
            "matched_skills": ", ".join(matched),
            "required_years": required_years if required_years is not None else "",
            "url": job.get("url", ""),
        })

    if filtered_out:
        logging.info("Filtered out %d jobs requiring more than %d years of experience.",
                     filtered_out, max_experience_years)

    ranked.sort(key=lambda row: row["score_percent"], reverse=True)
    return ranked


def write_csv(ranked_jobs: list[dict], out_path: str) -> None:
    """Writes ranked job rows to CSV (extra keys like job_key are excluded)."""
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES,
                                restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ranked_jobs)
    logging.info("Wrote %d rows to %s", len(ranked_jobs), out_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Rank scraped jobs against resume skills")
    parser.add_argument("jobs_json", help="Path to jobs_raw.json from scraper.py")
    parser.add_argument("matched_skills_txt", help="Path to a text file, one matched skill per line")
    parser.add_argument("--max-years", type=int, default=None,
                        help="Filter out jobs requiring more years of experience than this")
    parser.add_argument("--out", default=config.DEFAULT_OUTPUT_CSV, help="Output CSV path")
    args = parser.parse_args()

    with open(args.jobs_json, "r", encoding="utf-8") as f:
        loaded_jobs = json.load(f)
    with open(args.matched_skills_txt, "r", encoding="utf-8") as f:
        skills = [line.strip() for line in f if line.strip()]

    ranked_rows = rank_jobs(loaded_jobs, skills, max_experience_years=args.max_years)
    write_csv(ranked_rows, args.out)

    logging.info("Ranked %d jobs. Top 5:", len(ranked_rows))
    for row in ranked_rows[:5]:
        logging.info("  %s%% - %s @ %s", row["score_percent"], row["title"], row["company"])

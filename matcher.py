"""
matcher.py
Scores scraped job listings against a resume's matched skills using weighted
keyword matching (skills in the job title count more than skills in the
teaser/description), extracts required years of experience, normalizes
advertised salaries, detects work arrangement, and exports ranked results
to CSV and an HTML report.
"""
import csv
import html
import logging
import os
import re

import config
from resume_parser import skill_in_text

CSV_FIELDNAMES = [
    "score_percent", "title", "company", "location", "source",
    "work_arrangement", "salary", "salary_min", "salary_max",
    "listing_date", "status", "matched_skills", "required_years",
    "search_keyword", "first_seen", "new_this_run", "url",
]


# ======================================================
# SKILL MATCHING
# ======================================================
def _score_job(title: str, body: str,
               resume_skills: list[str]) -> tuple[float, list[str], list[str]]:
    """
    Weighted keyword score: a skill found in the job title counts
    TITLE_MATCH_WEIGHT, a skill found only in the body counts
    BODY_MATCH_WEIGHT.

    The percentage is normalized against a realistic strong match
    (config.TARGET_MATCH_SKILLS skills, weighted as if found in the title)
    rather than against every skill appearing in the title — that ceiling is
    unreachable, so it compressed every job into a narrow band near zero.
    Scores are capped at 100.
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
    target = min(len(resume_skills), max(1, config.TARGET_MATCH_SKILLS))
    max_weighted = target * config.TITLE_MATCH_WEIGHT
    score = min(100.0, weighted / max_weighted * 100)
    return round(score, 1), title_matches, body_matches


# ======================================================
# SCALE CALIBRATION
# ======================================================
def _weighted_from_matched(matched_skills: str) -> float:
    """Rebuilds a job's raw weighted score from its stored matched_skills."""
    parts = [part.strip() for part in (matched_skills or "").split(",")
             if part.strip()]
    title_hits = sum(1 for part in parts if part.endswith("(title)"))
    body_hits = len(parts) - title_hits
    return (title_hits * config.TITLE_MATCH_WEIGHT
            + body_hits * config.BODY_MATCH_WEIGHT)


def suggest_target_match(stored_rows: list[dict]) -> dict:
    """
    Derives a TARGET_MATCH_SKILLS value from stored jobs.

    The scale is pinned to the TOP of the distribution, not the middle: in a
    job search most advertisements genuinely are not a match, so a low median
    is correct and a scale forced to centre on 50 would flatter bad jobs. A
    good value puts the strongest job in the corpus at 80-95 — high enough to
    read as "excellent", short of the 100 clamp that would erase the ordering
    between the best few.

    Returns {"suggested": int|None, "sample": int,
             "table": [(k, median, p90, max)]}.
    """
    weights = sorted(_weighted_from_matched(row.get("matched_skills", ""))
                     for row in stored_rows)
    if len(weights) < config.CALIBRATION_MIN_JOBS:
        return {"suggested": None, "sample": len(weights), "table": []}

    table = []
    best = None
    for target in range(3, 16):
        scaled = [min(100.0, weight / (target * config.TITLE_MATCH_WEIGHT) * 100)
                  for weight in weights]
        top = max(scaled)
        table.append((target, round(scaled[len(scaled) // 2], 1),
                      round(scaled[int(len(scaled) * 0.90)], 1), round(top, 1)))
        if best is None and 80 <= top <= 95:
            best = target
    return {"suggested": best, "sample": len(weights), "table": table}


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
# SALARY NORMALIZATION
# ======================================================
_SALARY_NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _parse_salary(salary_text: str) -> tuple[int | None, int | None]:
    """
    Normalizes an advertised salary like "₱50,000 – ₱70,000 per month" to
    numeric monthly (salary_min, salary_max). Yearly amounts are divided
    by 12. Hourly/daily/weekly rates, dollar amounts (OnlineJobs.ph pays in
    USD — mixing currencies would be misleading), and blank text return
    (None, None).
    """
    if not salary_text:
        return None, None
    text_lower = salary_text.lower()
    if "$" in salary_text or "usd" in text_lower:
        return None, None
    if re.search(r"(?:per|an?)\s+(hour|day|week)|hourly|daily|weekly", text_lower):
        return None, None

    numbers = [float(number.replace(",", ""))
               for number in _SALARY_NUMBER_PATTERN.findall(salary_text)]
    numbers = [number for number in numbers if number >= 1000]  # skip "13th month" etc.
    if not numbers:
        return None, None

    if re.search(r"(?:per|a)\s+(year|annum)|annually|yearly|/\s*yr", text_lower):
        numbers = [number / 12 for number in numbers]

    return round(min(numbers)), round(max(numbers))


# ======================================================
# WORK ARRANGEMENT DETECTION
# ======================================================
_ARRANGEMENT_PATTERNS = [
    ("Hybrid", re.compile(r"\bhybrid\b", re.IGNORECASE)),
    ("Remote", re.compile(r"\b(remote|work[ -]from[ -]home|wfh)\b", re.IGNORECASE)),
    ("On-site", re.compile(r"\b(on[ -]?site|office[ -]based)\b", re.IGNORECASE)),
]


def _detect_work_arrangement(job_text: str) -> str:
    """
    Detects remote/hybrid/on-site from the job text. Hybrid wins over
    remote (ads often say "hybrid remote setup"). Returns "" when the ad
    doesn't say.
    """
    for label, pattern in _ARRANGEMENT_PATTERNS:
        if pattern.search(job_text):
            return label
    return ""


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

        salary_text = job.get("salary", "")
        salary_min, salary_max = _parse_salary(salary_text)
        matched = [f"{skill} (title)" for skill in title_matches] + body_matches
        location = job.get("location", "")
        ranked.append({
            "job_key": job.get("job_key", ""),
            "score_percent": score,
            "title": title,
            "company": job.get("company", ""),
            "location": location,
            "source": job.get("source", ""),
            "work_arrangement": _detect_work_arrangement(
                f"{title} {location} {body}"),
            "salary": salary_text,
            "salary_min": salary_min if salary_min is not None else "",
            "salary_max": salary_max if salary_max is not None else "",
            "listing_date": job.get("listing_date", ""),
            "status": "new",
            "matched_skills": ", ".join(matched),
            "required_years": required_years if required_years is not None else "",
            "search_keyword": job.get("search_keyword", ""),
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


_HTML_STYLE = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }
h1 { font-size: 20px; }
p.meta { color: #666; font-size: 13px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f0f3f7; position: sticky; top: 0; }
tr:nth-child(even) { background: #fafafa; }
a { color: #0b5fa5; text-decoration: none; }
a:hover { text-decoration: underline; }
.badge { background: #1a7f37; color: #fff; border-radius: 4px; padding: 1px 6px; font-size: 11px; }
.score { font-weight: 600; }
.skills { color: #555; font-size: 12px; }
"""


def _html_report_row(row: dict) -> str:
    """Renders one ranked job as an HTML table row."""
    title_link = (f'<a href="{html.escape(row.get("url", ""), quote=True)}" '
                  f'target="_blank">{html.escape(row.get("title", ""))}</a>')
    new_badge = ' <span class="badge">NEW</span>' if row.get("new_this_run") == "yes" else ""
    cells = [
        f'<td class="score">{row.get("score_percent", "")}%</td>',
        f"<td>{title_link}{new_badge}</td>",
        f"<td>{html.escape(str(row.get('company', '')))}</td>",
        f"<td>{html.escape(str(row.get('location', '')))}</td>",
        f"<td>{html.escape(str(row.get('source', '') or ''))}</td>",
        f"<td>{html.escape(str(row.get('work_arrangement', '') or ''))}</td>",
        f"<td>{html.escape(str(row.get('salary', '') or ''))}</td>",
        f"<td>{html.escape(str(row.get('listing_date', '') or ''))}</td>",
        f"<td>{html.escape(str(row.get('status', '') or ''))}</td>",
        f'<td class="skills">{html.escape(str(row.get("matched_skills", "")))}</td>',
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def write_html_report(ranked_jobs: list[dict], out_path: str,
                      generated_note: str = "") -> None:
    """Writes a browsable ranked report with clickable job links."""
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    header_cells = ["Score", "Job", "Company", "Location", "Source",
                    "Arrangement", "Salary", "Posted", "Status",
                    "Matched skills"]
    rows_html = "\n".join(_html_report_row(row) for row in ranked_jobs)
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Ranked job matches</title>"
        f"<style>{_HTML_STYLE}</style></head><body>"
        f"<h1>Ranked job matches ({len(ranked_jobs)})</h1>"
        f"<p class='meta'>{html.escape(generated_note)}</p>"
        "<table><thead><tr>"
        + "".join(f"<th>{cell}</th>" for cell in header_cells)
        + f"</tr></thead><tbody>\n{rows_html}\n</tbody></table></body></html>"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(document)
    logging.info("Wrote HTML report to %s", out_path)

"""
matcher.py
Scores scraped job listings against a resume's matched skills (simple keyword
overlap) and writes a ranked CSV.
"""
import argparse
import csv
import json
import re


def score_job(job_text: str, resume_skills: list[str]) -> tuple[float, list[str]]:
    """
    Returns (score_percent, matched_skills) where score is the percentage of
    resume_skills found (case-insensitive, whole word/phrase) in job_text.
    """
    text_lower = job_text.lower()
    matched = []
    for skill in resume_skills:
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, text_lower):
            matched.append(skill)

    score = (len(matched) / len(resume_skills) * 100) if resume_skills else 0.0
    return round(score, 1), matched


def rank_jobs(jobs_path: str, resume_skills: list[str]) -> list[dict]:
    with open(jobs_path, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    ranked = []
    for job in jobs:
        combined_text = f"{job.get('title', '')} {job.get('teaser', '')}"
        score, matched = score_job(combined_text, resume_skills)
        ranked.append({
            "score_percent": score,
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "matched_skills": ", ".join(matched),
            "url": job.get("url", ""),
        })

    ranked.sort(key=lambda x: x["score_percent"], reverse=True)
    return ranked


def write_csv(ranked_jobs: list[dict], out_path: str):
    fieldnames = ["score_percent", "title", "company", "location", "matched_skills", "url"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ranked_jobs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rank scraped jobs against resume skills")
    parser.add_argument("jobs_json", help="Path to jobs_raw.json from scraper.py")
    parser.add_argument("matched_skills_txt", help="Path to a text file, one matched skill per line")
    parser.add_argument("--out", default="ranked_jobs.csv", help="Output CSV path")
    args = parser.parse_args()

    with open(args.matched_skills_txt, "r", encoding="utf-8") as f:
        skills = [line.strip() for line in f if line.strip()]

    ranked = rank_jobs(args.jobs_json, skills)
    write_csv(ranked, args.out)

    print(f"Ranked {len(ranked)} jobs. Top 5:")
    for job in ranked[:5]:
        print(f"  {job['score_percent']}% - {job['title']} @ {job['company']}")
    print(f"\nSaved to {args.out}")

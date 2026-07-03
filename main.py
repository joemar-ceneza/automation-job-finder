"""
main.py
Full pipeline: parse resume -> scrape JobStreet PH -> score & rank jobs -> CSV.

Usage:
    python main.py resume.pdf "python developer" --skills skills.txt --pages 2
"""
import argparse

from resume_parser import extract_text_from_pdf, load_skills, find_matching_skills
from scraper import scrape_jobs
from matcher import rank_jobs, write_csv
from dataclasses import asdict


def run(resume_path: str, keyword: str, skills_path: str, pages: int,
        delay: float, debug: bool, out_csv: str):
    print("Step 1/3: Parsing resume...")
    resume_text = extract_text_from_pdf(resume_path)
    all_skills = load_skills(skills_path)
    resume_skills = find_matching_skills(resume_text, all_skills)
    print(f"  Found {len(resume_skills)}/{len(all_skills)} skills in resume: {', '.join(resume_skills)}")

    if not resume_skills:
        print("  WARNING: No skills matched. Edit skills.txt to include terms from your resume.")

    print(f"\nStep 2/3: Scraping JobStreet PH for '{keyword}'...")
    jobs = scrape_jobs(keyword, max_pages=pages, delay_seconds=delay, debug=debug)

    if not jobs:
        print("  No jobs scraped. Run with --debug to inspect debug_page.html and fix selectors.")
        return

    jobs_json_path = "jobs_raw_temp.json"
    import json
    with open(jobs_json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(j) for j in jobs], f, indent=2, ensure_ascii=False)

    print(f"\nStep 3/3: Scoring {len(jobs)} jobs against your resume skills...")
    ranked = rank_jobs(jobs_json_path, resume_skills)
    write_csv(ranked, out_csv)

    print(f"\nDone. Top matches:")
    for job in ranked[:10]:
        print(f"  {job['score_percent']}% - {job['title']} @ {job['company']}")
    print(f"\nFull ranked list saved to {out_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resume-to-JobStreet job matcher")
    parser.add_argument("resume_pdf", help="Path to your resume PDF")
    parser.add_argument("keyword", help="Job title/keyword to search, e.g. 'python developer'")
    parser.add_argument("--skills", default="skills.txt", help="Path to skills keyword list")
    parser.add_argument("--pages", type=int, default=2, help="Number of search-result pages to scrape")
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds between page requests")
    parser.add_argument("--debug", action="store_true", help="Run browser visibly, save debug_page.html")
    parser.add_argument("--out", default="ranked_jobs.csv", help="Output CSV path")
    args = parser.parse_args()

    run(args.resume_pdf, args.keyword, args.skills, args.pages, args.delay, args.debug, args.out)

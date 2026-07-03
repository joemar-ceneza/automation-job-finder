# Resume-to-Job Matcher (JobStreet PH)

## What This Does
Scrapes job listings from JobStreet Philippines, scores them against your
resume's skills using weighted keyword matching (skills in the job title
count more than skills in the description), and saves results to a local
SQLite database plus a ranked CSV. Jobs are deduplicated and tracked across
runs, so re-running only scores listings you haven't seen before.

## Requirements
- Python 3.10+
- Windows OS (works elsewhere too)
- No credentials needed — only public pages are scraped

## Setup
1. Clone or download this project
2. Create a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   playwright install chromium
   ```
4. Edit `skills.txt` — replace the example entries with the actual skills,
   tools, and keywords from your resume (one per line). The script only
   scores jobs against what's in this file, so make it accurate.

## How to Run
```
python main.py path\to\resume.pdf "python developer" --pages 2
```

More thorough (visits each job's detail page for the full description, and
filters out jobs asking for more than 3 years of experience):
```
python main.py resume.pdf "python developer" --full-desc --max-years 3
```

The pipeline:
1. Extracts text from your resume PDF and matches it against `skills.txt`
2. Searches JobStreet PH for your keyword, deduplicating reposted listings
   by job URL (fallback: normalized title+company)
3. Checks `output/jobs.db` and scores only jobs not seen in previous runs
4. Saves everything to SQLite and exports a ranked CSV — new listings are
   flagged `new_this_run = yes`

## Options

| Flag          | Default                  | Description                                        |
|---------------|--------------------------|----------------------------------------------------|
| `--skills`    | `skills.txt`             | Path to your skills keyword file                   |
| `--pages`     | `2`                      | Number of search-result pages to scrape            |
| `--delay`     | `3.0`                    | Seconds between page requests (also rate-limits detail pages) |
| `--full-desc` | off                      | Visit each job's detail page for the full description (slower, more accurate scoring) |
| `--max-years` | off                      | Your years of experience — jobs requiring more are filtered out |
| `--only-new`  | off                      | Export only jobs not seen in previous runs to the CSV |
| `--debug`     | off                      | Run browser visibly, save page HTML for every page |
| `--out`       | `output/ranked_jobs.csv` | Output CSV path                                    |

## How scoring works
- A skill found in the **job title** counts ×3; a skill found only in the
  teaser/full description counts ×1 (weights in `config.py`).
- **Aliases**: alternate spellings count as matches — e.g. "ReactJS" or
  "React.js" in a posting matches your "React JS" skill. Extend the
  `SKILL_ALIASES` map in `config.py` when you add skills to `skills.txt`.
- Duplicate lines in `skills.txt` are ignored so a repeated skill can't be
  double-counted.
- The percentage is normalized against the maximum possible score, so
  numbers are lower than the old unweighted version — compare jobs against
  each other, not against the old scores.
- A regex extractor pulls "required years of experience" phrases (e.g.
  "at least 5 years", "3-5 years experience") into the `required_years`
  CSV column; `--max-years` uses it to filter.
- **Salary**: advertised salaries (e.g. "₱50,000 per month") are captured
  from search cards — and from detail pages with `--full-desc` — into the
  `salary` column. Many ads don't state one, so blanks are normal.

## Persistence
- `output/jobs.db` (SQLite) is the source of truth. Each job stores its
  score, matched skills, required years, description, `first_seen`, and
  `last_seen` timestamps.
- Already-seen jobs are not re-scored; their stored score appears in the
  CSV with `new_this_run = no`. Note: if you later switch to `--full-desc`,
  previously seen jobs keep their teaser-based score. Delete `output/jobs.db`
  to start fresh.
- Inspect the db anytime: `sqlite3 output/jobs.db "SELECT title, score_percent, first_seen FROM jobs ORDER BY score_percent DESC LIMIT 20"`

## If scraping returns 0 results
JobStreet updates their page markup periodically, which breaks selectors.
When a page yields 0 listings the current HTML is now saved **automatically**
to `logs/debug_no_results_*.html`. To fix:

1. Open the saved HTML in a browser
2. Inspect the job card elements and update the `SELECTORS` dict in
   `config.py` to match the current attribute names/classes

Failed page loads are retried 3 times with exponential backoff before giving
up, and a screenshot is saved to `logs/screenshots/` on hard failures.

## Important notes
- **Rate limiting**: keep `--pages` low. The 3-second delay between requests
  (including detail-page visits with `--full-desc`) is intentional — don't
  remove it.
- **Terms of Service**: JobStreet's ToS generally restricts automated
  scraping. This script is intended for personal, non-commercial job
  searching at low volume, not for building a job board or reselling data.
  Use at your own discretion.
- **No login required**: only public search-result and job-detail pages are
  read; no account credentials are used or stored.

## Project Structure
```
auto-find-job/
├── main.py            # Entry point — orchestrates the full workflow
├── config.py          # All settings, selectors, weights, and paths
├── utils.py           # Generic retry helper (exponential backoff)
├── resume_parser.py   # Extracts text from PDF resume, matches skills.txt
├── scraper.py         # Playwright scraper (search results + detail pages)
├── matcher.py         # Weighted scoring, experience extraction, CSV export
├── db_handler.py      # SQLite persistence (output/jobs.db)
├── skills.txt         # Your customizable skill/keyword list
├── logs/              # automation.log, debug HTML, error screenshots
└── output/            # jobs.db and ranked_jobs.csv
```

## Logs
Logs are saved to `logs/automation.log`.
Debug page HTML is saved to `logs/debug_*.html`.
Screenshots on errors are saved to `logs/screenshots/`.

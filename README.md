# Resume-to-Job Matcher (JobStreet PH, OnlineJobs.ph, Indeed PH, RemoteOK, Remotive)

## What This Does
Scrapes job listings from **JobStreet PH**, **OnlineJobs.ph**, and
**Indeed PH** (plus the **RemoteOK** and **Remotive** remote-job APIs),
scores them against your resume's skills using weighted
keyword matching (skills in the job title count more than skills in the
description), and saves results to a local SQLite database plus a ranked
CSV and HTML report. Jobs are deduplicated and tracked across runs, and a
local **Streamlit dashboard** lets you browse, filter, and record which
jobs you applied to with a click — making it a lightweight job-search
tracker, not just a scraper. It can also email you a digest of new matches.

## Requirements
- Python 3.10+
- Windows OS (works elsewhere too)
- No credentials needed for scraping — only public pages are read
- Optional: a Gmail account with an App Password, only for the `--email` digest

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
4. Create your skill list — either edit `skills.txt` by hand (one skill
   per line), or draft it automatically from your resume:
   ```
   python main.py path\to\resume.pdf --generate-skills
   ```
   This writes `skills_draft.txt` (it never touches `skills.txt`).
   Review it, delete anything that doesn't reflect your skills, then
   replace `skills.txt` with it. The script only scores jobs against
   what's in `skills.txt`, so make it accurate.
5. (Only for `--email`) Copy `.env.example` to `.env` and fill in your
   Gmail address and an App Password
   (https://myaccount.google.com/apppasswords — requires 2-Step Verification).

## How to Run
```
python main.py path\to\resume.pdf "python developer" --pages 2
```

Search several roles at once, limited to a location:
```
python main.py resume.pdf "python developer, automation engineer" --location "Metro Manila"
```

More thorough — full descriptions, filter jobs asking for over 3 years of
experience, hide weak matches and low salaries, email me the new ones:
```
python main.py resume.pdf "python developer" --full-desc --max-years 3 --min-score 15 --min-salary 40000 --email
```

Search specific sites only:
```
python main.py resume.pdf "python developer" --site jobstreet,onlinejobs
```

Browse results and record applications in the dashboard (recommended):
```
streamlit run dashboard.py
```

Or record what you did with a job from the terminal (any site's job URL works):
```
python main.py --set-status https://ph.jobstreet.com/job/12345678 applied
```

Archive listings that have vanished from search results for 30+ days:
```
python main.py --prune-days 30
```

The pipeline:
1. Extracts text from your resume PDF and matches it against `skills.txt`
2. Searches each selected site for each keyword (comma-separated),
   deduplicating reposted listings by site-prefixed job id
   (fallback: normalized title+company)
3. Checks `output/jobs.db` and scores only jobs not seen in previous runs
4. Saves everything to SQLite and exports a ranked CSV + `output/report.html`
   — new listings are flagged `new_this_run = yes`
5. With `--email`, sends a Gmail digest of the new matches

## Job sites

| Site | Notes |
|------|-------|
| `jobstreet` | JobStreet PH. Supports `--location` and `--full-desc`. |
| `onlinejobs` | OnlineJobs.ph (remote jobs for PH workers). All listings are work-from-home; salaries are usually **USD** and kept as raw text (not converted into the peso `salary_min/max` columns). Employer names aren't shown on search cards. |
| `indeed` | Indeed PH. Sits behind Cloudflare anti-bot protection — it worked in testing, but expect **intermittent blocks**; when blocked, the scraper logs a warning, saves the page HTML to `logs/`, and the other sites continue normally. No posting date on search cards. |
| `remoteok` | RemoteOK global remote jobs via their **public JSON API** — no browser, nothing to break. The free feed only exposes the ~100 most recent listings, so matches per keyword are few. USD salaries kept as raw text. Full descriptions always included. |
| `remotive` | Remotive global remote jobs via their **public JSON API** with server-side keyword search. Check the location column — some listings are restricted to specific regions. Full descriptions always included. |

## Options

| Flag           | Default                  | Description                                        |
|----------------|--------------------------|----------------------------------------------------|
| `--site`       | all five                 | Comma-separated sites: `jobstreet`, `onlinejobs`, `indeed`, `remoteok`, `remotive` |
| `--generate-skills` | —                   | Draft `skills_draft.txt` from your resume PDF, then exit |
| `--skills`     | `skills.txt`             | Path to your skills keyword file                   |
| `--pages`      | `2`                      | Search-result pages to scrape per keyword          |
| `--delay`      | `3.0`                    | Seconds between page requests (also rate-limits detail pages) |
| `--location`   | off                      | Limit results to a location, e.g. `"Metro Manila"` (JobStreet + Indeed; OnlineJobs is remote-only) |
| `--full-desc`  | off                      | Visit each job's detail page for the full description (slower, more accurate scoring) |
| `--max-years`  | off                      | Your years of experience — jobs requiring more are filtered out |
| `--min-score`  | off                      | Exclude jobs scoring below this percentage from exports |
| `--min-salary` | off                      | Exclude jobs whose stated max monthly salary (PHP) is below this; jobs with no stated salary are kept |
| `--only-new`   | off                      | Export only jobs not seen in previous runs         |
| `--rescore`    | off                      | Re-score all stored jobs against the current skill list |
| `--prune-days` | off                      | Archive jobs not seen in N days (standalone or during a run) |
| `--set-status` | —                        | `--set-status <job_key or URL> <status>` records e.g. applied/interested/rejected, then exits (any site's job URL works) |
| `--email`      | off                      | Email a digest of new matches via Gmail SMTP (needs `.env`) |
| `--debug`      | off                      | Run browser visibly, save page HTML for every page |
| `--out`        | `output/ranked_jobs.csv` | Output CSV path                                    |
| `--html`       | `output/report.html`     | Output HTML report path                            |

## How scoring works
- A skill found in the **job title** counts ×3; a skill found only in the
  teaser/full description counts ×1 (weights in `config.py`).
- **Aliases**: alternate spellings count as matches — e.g. "ReactJS" or
  "React.js" in a posting matches your "React JS" skill. Extend the
  `SKILL_ALIASES` map in `config.py` when you add skills to `skills.txt`.
- Duplicate lines in `skills.txt` are ignored so a repeated skill can't be
  double-counted.
- The percentage is normalized against the maximum possible score —
  compare jobs against each other, not against 100.
- A regex extractor pulls "required years of experience" phrases (e.g.
  "at least 5 years", "3-5 years experience") into the `required_years`
  column; `--max-years` uses it to filter.
- **Salary**: advertised salaries (e.g. "₱50,000 per month") are captured
  from search cards — and from detail pages with `--full-desc` — and
  normalized into numeric monthly `salary_min`/`salary_max` columns
  (yearly amounts ÷12; hourly/daily rates and USD amounts are left
  unparsed to avoid mixing currencies). Many ads don't state one, so
  blanks are normal.
- **Work arrangement**: Remote / Hybrid / On-site is detected from the ad
  text into the `work_arrangement` column when the ad mentions it.
- **Posting date**: JobStreet's "3d ago" is converted to an absolute date
  in the `listing_date` column at scrape time.

## Dashboard
```
streamlit run dashboard.py
```
Opens a local web page (nothing is hosted online) showing every stored job
with search, status/site filters, minimum score/salary sliders, and headline
counts. Change any row's **Status** dropdown (new / interested / applied /
rejected / no answer) and click **Save status changes** — it writes straight
to `output/jobs.db`. Job titles link to the original posting. Scraping still
happens via `main.py`; run it (or schedule it) to refresh the data, then
just refresh the dashboard page.

## Persistence & tracking
- `output/jobs.db` (SQLite) is the source of truth. Each job stores its
  score, matched skills, required years, salary, description, posting date,
  `status`, and `first_seen`/`last_seen` timestamps.
- Already-seen jobs are not re-scored; their stored score appears in the
  CSV with `new_this_run = no`.
- **Status tracking**: every job starts as `new`. Use `--set-status` to
  record `interested`, `applied`, `rejected`, or anything else — it shows
  in the CSV/HTML `status` column on every future run.
- **Skill list changes**: the pipeline stores a fingerprint of your matched
  skills. If it changes, you'll get a warning that stored scores are stale —
  run once with `--rescore` to refresh them (uses stored descriptions; no
  re-scraping).
- **Pruning**: `--prune-days N` archives jobs whose `last_seen` is older
  than N days. Archived jobs disappear from exports but are NOT deleted,
  and are automatically un-archived if they reappear in search results.
- Inspect the db anytime: `sqlite3 output/jobs.db "SELECT title, score_percent, status FROM jobs ORDER BY score_percent DESC LIMIT 20"`

## Email digest
`--email` sends the run's new matches (title, score, salary, matched skills,
links) to `EMAIL_RECIPIENT` via Gmail SMTP. Configure `.env` first (see
`.env.example`); the digest is skipped with a clear log message when
credentials are missing. Combine with `--min-score` so the email only
contains matches worth reading. Scheduled daily via Task Scheduler +
`--email`, this becomes a hands-off job alert.

## If scraping returns 0 results
Job sites update their page markup periodically, which breaks selectors.
When a page yields 0 listings the current HTML is saved **automatically**
to `logs/debug_*_no_results_*.html`. To fix:

1. Open the saved HTML in a browser
2. Inspect the job card elements and update that site's entry in the
   `SELECTORS` dict in `config.py` to match the current attribute
   names/classes

For Indeed specifically, 0 results usually means a Cloudflare block
(saved as `debug_indeed_blocked_*.html`) — try again later or run fewer
pages; the other sites are unaffected.

Failed page loads are retried 3 times with exponential backoff before giving
up, and a screenshot is saved to `logs/screenshots/` on hard failures.

## Important notes
- **Rate limiting**: keep `--pages` and the keyword count low. The 3-second
  delay between requests (including detail-page visits with `--full-desc`)
  is intentional — don't remove it.
- **Terms of Service**: JobStreet's ToS generally restricts automated
  scraping. This script is intended for personal, non-commercial job
  searching at low volume, not for building a job board or reselling data.
  Use at your own discretion.
- **No login required**: only public search-result and job-detail pages are
  read; no JobStreet credentials are used or stored. The only credentials
  in the project are your own Gmail App Password in `.env` (never
  committed) for the optional digest.

## Project Structure
```
auto-find-job/
├── main.py                # Entry point — orchestrates the full workflow
├── dashboard.py           # Streamlit dashboard (streamlit run dashboard.py)
├── config.py              # All settings, per-site selectors, weights, paths
├── utils.py               # Generic retry helper (exponential backoff)
├── resume_parser.py       # Extracts text from PDF resume, matches skills.txt
├── scraper_common.py      # Shared scraper pieces (JobListing, keys, dates)
├── scraper_jobstreet.py   # JobStreet PH scraper
├── scraper_onlinejobs.py  # OnlineJobs.ph scraper
├── scraper_indeed.py      # Indeed PH scraper (Cloudflare-aware)
├── scraper_remoteok.py    # RemoteOK JSON API client
├── scraper_remotive.py    # Remotive JSON API client
├── matcher.py             # Weighted scoring, salary/years extraction, CSV + HTML export
├── db_handler.py          # SQLite persistence, status tracking, prune/rescore
├── email_handler.py       # Gmail SMTP digest of new matches
├── skills.txt             # Your customizable skill/keyword list
├── .env.example           # Template for Gmail credentials (copy to .env)
├── logs/                  # automation.log, debug HTML, error screenshots
└── output/                # jobs.db, ranked_jobs.csv, report.html
```

## Logs
Logs are saved to `logs/automation.log`.
Debug page HTML is saved to `logs/debug_*.html`.
Screenshots on errors are saved to `logs/screenshots/`.

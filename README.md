# Resume-to-Job Matcher (JobStreet PH)

Scrapes job listings from JobStreet Philippines, compares them against your
resume's skills, and outputs a ranked CSV of best-fit jobs.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## 1. Customize your skills list

Edit `skills.txt` — replace the example entries with the actual skills,
tools, and keywords from your resume (one per line). The script only scores
jobs against what's in this file, so make it accurate.

## 2. Run the full pipeline

```bash
python main.py path/to/resume.pdf "python developer" --pages 2
```

This will:
1. Extract text from your resume PDF and match it against `skills.txt`
2. Search JobStreet PH for the keyword you gave (e.g. "python developer")
3. Score each job listing by how many of your matched skills appear in the
   job title/description
4. Save a ranked CSV to `ranked_jobs.csv` (highest match % first)

## Options

| Flag       | Default          | Description                                  |
|------------|------------------|-----------------------------------------------|
| `--skills` | `skills.txt`     | Path to your skills keyword file             |
| `--pages`  | `2`              | Number of search-result pages to scrape      |
| `--delay`  | `3.0`            | Seconds to wait between page requests        |
| `--debug`  | off              | Run browser visibly, save `debug_page.html`  |
| `--out`    | `ranked_jobs.csv`| Output CSV path                              |

## If scraping returns 0 results

JobStreet updates their page markup periodically, which can break the
selectors in `scraper.py`. To fix:

1. Run with `--debug`: `python main.py resume.pdf "keyword" --debug`
2. Open the generated `debug_page.html` in a browser
3. Inspect the job card elements and update the `SELECTORS` dict at the top
   of `scraper.py` to match the current attribute names/classes

## Important notes

- **Rate limiting**: keep `--pages` and frequency low. The default 3-second
  delay between requests is intentional — don't remove it.
- **Terms of Service**: JobStreet's ToS generally restricts automated
  scraping. This script is intended for personal, non-commercial job
  searching at low volume, not for building a job board or reselling data.
  Use at your own discretion.
- **No login required**: this only scrapes public search-result pages, no
  account credentials are used or stored.

## Files

- `resume_parser.py` — extracts text from PDF resume, matches against skills.txt
- `scraper.py` — Playwright scraper for JobStreet PH search results
- `matcher.py` — scores and ranks scraped jobs against matched resume skills
- `main.py` — runs the full pipeline end-to-end
- `skills.txt` — your customizable skill/keyword list

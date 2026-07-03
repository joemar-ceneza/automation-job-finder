"""
config.py
All settings, constants, and configuration for the JobStreet job matcher.

No credentials are needed for this project (it only reads public pages),
so there is no .env. If credentials are ever added, load them from .env.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ======================================================
# PATHS (absolute — safe for Windows Task Scheduler)
# ======================================================
LOGS_DIR = os.path.join(BASE_DIR, "logs")
SCREENSHOTS_DIR = os.path.join(LOGS_DIR, "screenshots")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_FILE = os.path.join(LOGS_DIR, "automation.log")
DB_PATH = os.path.join(OUTPUT_DIR, "jobs.db")
DEFAULT_SKILLS_FILE = os.path.join(BASE_DIR, "skills.txt")
DEFAULT_OUTPUT_CSV = os.path.join(OUTPUT_DIR, "ranked_jobs.csv")

# ======================================================
# SCRAPING
# ======================================================
BASE_URL = "https://ph.jobstreet.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
HEADLESS = True  # --debug overrides this to run the browser visibly
DEFAULT_PAGES = 2
DEFAULT_DELAY_SECONDS = 3.0  # politeness delay between requests — keep >= 3
PAGE_LOAD_TIMEOUT_MS = 30000
RENDER_WAIT_MS = 2000  # settle time for JS-rendered search results
DETAIL_WAIT_TIMEOUT_MS = 10000

# Centralized selectors — patch here when JobStreet changes its markup.
SELECTORS = {
    "job_card": "article",
    "job_title": "a[data-automation='jobTitle']",
    "job_company": "a[data-automation='jobCompany'], span[data-automation='jobCompany']",
    "job_location": "span[data-automation='jobLocation']",
    "job_teaser": "span[data-automation='jobShortDescription']",
    "job_salary": "span[data-automation='jobSalary']",
    "job_detail_description": "div[data-automation='jobAdDetails']",
    "job_detail_salary": "span[data-automation='job-detail-salary']",
}

# ======================================================
# RETRY
# ======================================================
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
RETRY_BACKOFF = 2.0  # delay doubles after each failed attempt

# ======================================================
# MATCHING
# ======================================================
TITLE_MATCH_WEIGHT = 3.0  # a skill found in the job title
BODY_MATCH_WEIGHT = 1.0   # a skill found only in the teaser/description
MAX_PLAUSIBLE_YEARS = 20  # ignore "years" numbers above this (e.g. "25 years in business")

# Alternate spellings for skills.txt entries. A skill counts as matched if
# the skill itself OR any alias appears (whole-word) in the text. Keys must
# match skills.txt lines exactly.
SKILL_ALIASES = {
    "React JS": ["ReactJS", "React.js", "React"],
    "Next JS": ["NextJS", "Next.js"],
    "Node JS": ["NodeJS", "Node.js"],
    "Express JS": ["ExpressJS", "Express.js", "Express"],
    "JavaScript ES6": ["JavaScript", "ES6"],
    "HTML 5": ["HTML5", "HTML"],
    "CSS 3": ["CSS3", "CSS"],
    "Bootstrap 5": ["Bootstrap"],
    "Tailwind CSS": ["Tailwind", "TailwindCSS"],
    "Sass": ["SCSS"],
    "REST API": ["RESTful API", "REST APIs", "RESTful"],
    "API Integration": ["API Integrations"],
    "Web Scraping": ["Web Scraper", "Scraping"],
    "PostgreSQL": ["Postgres"],
    "GitHub (Version Control)": ["GitHub"],
    "Bash Command Line": ["Bash"],
}

"""
config.py
All settings, constants, and configuration for the JobStreet job matcher.

Scraping needs no credentials (only public pages are read). The optional
--email digest sends via Gmail SMTP; its credentials live in .env
(see .env.example) and are loaded by email_handler.py — never here.
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
LOG_MAX_BYTES = 5 * 1024 * 1024  # rotate automation.log at 5 MB
LOG_BACKUP_COUNT = 3  # keep automation.log.1 … .3
DB_PATH = os.path.join(OUTPUT_DIR, "jobs.db")
BACKUP_DIR = os.path.join(OUTPUT_DIR, "backups")
BACKUP_KEEP = 10  # oldest backups beyond this are deleted
DEFAULT_SKILLS_FILE = os.path.join(BASE_DIR, "skills.txt")
DEFAULT_SKILLS_DRAFT = os.path.join(BASE_DIR, "skills_draft.txt")

# The master resume: structured Markdown that is the source of truth once
# imported. Everything downstream reads this, never the PDF.
MASTER_RESUME_FILE = os.path.join(BASE_DIR, "master_resume.md")
DOCUMENTS_DIR = os.path.join(OUTPUT_DIR, "documents")
DOCUMENT_FORMATS = ["md", "docx", "pdf"]

# Sections pushed to the top when tailoring a resume to a job. Anything not
# listed keeps its relative position after these.
RESUME_SECTION_PRIORITY = ["Summary", "Skills", "Experience", "Projects",
                           "Education", "Certifications"]
DEFAULT_OUTPUT_CSV = os.path.join(OUTPUT_DIR, "ranked_jobs.csv")
DEFAULT_OUTPUT_HTML = os.path.join(OUTPUT_DIR, "report.html")

# ======================================================
# SCRAPING
# ======================================================
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

# Sites searched when --site isn't given. Each name maps to a
# scraper_<name>.py module with a run_scraper() entry point.
DEFAULT_SITES = ["jobstreet", "onlinejobs"]

# Companies whose listings are skipped entirely — never scored, stored, or
# shown. Case-insensitive substring match on the company name, so "acme"
# also blocks "ACME Recruitment Inc". Note: OnlineJobs.ph hides employer
# names on search cards, so those listings can't be blocked by company.
BLOCKLISTED_COMPANIES: list[str] = [
    # "Example Recruitment Agency",
]

# Job titles containing any of these are skipped. Matched as whole words, so
# "lead" blocks "Lead Developer" but not "Leadership Trainee", and "manager"
# does not block "Management Trainee". This cuts far more noise than the
# company blocklist, because every site shows a title.
BLOCKLISTED_TITLE_KEYWORDS: list[str] = [
    # "senior", "lead", "manager", "intern", "sales", ".net",
]

# Company names that identify nobody. JobStreet uses "Private Advertiser" for
# any employer posting anonymously, so treating it as a company would merge
# unrelated firms into one. Duplicate detection ignores these entirely.
PLACEHOLDER_COMPANIES = {
    "private advertiser", "confidential", "anonymous", "undisclosed",
    "recruitment agency", "n a",
}

JOBSTREET_BASE_URL = "https://ph.jobstreet.com"
ONLINEJOBS_BASE_URL = "https://www.onlinejobs.ph"

# Centralized selectors per site — patch here when a site changes its markup.
SELECTORS = {
    "jobstreet": {
        "job_card": "article",
        "job_title": "a[data-automation='jobTitle']",
        "job_company": "a[data-automation='jobCompany'], span[data-automation='jobCompany']",
        "job_location": "span[data-automation='jobLocation']",
        "job_teaser": "span[data-automation='jobShortDescription']",
        "job_salary": "span[data-automation='jobSalary']",
        "job_listing_date": "[data-automation='jobListingDate']",
        "job_detail_description": "div[data-automation='jobAdDetails']",
        "job_detail_salary": "span[data-automation='job-detail-salary']",
    },
    "onlinejobs": {
        "job_card": "div.jobpost-cat-box.latest-job-post",
        "job_title": "h4",
        "job_title_badge": "h4 span.badge",
        "job_link": "a[href*='/jobseekers/job/']",
        "job_teaser": "div.desc",
        "job_salary": "dl:has(i.icon-round-dollar) dd",
        "job_listing_date": "p[data-temp]",
        "job_detail_description": "#job-description",
    },
}

# ======================================================
# RETRY
# ======================================================
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
RETRY_BACKOFF = 2.0  # delay doubles after each failed attempt

# ======================================================
# APPLICATION LIFECYCLE (dashboard + --set-status)
# ======================================================
# The stages themselves and their legal transitions live in stages.py.
# An application awaiting a reply for this many days is suggested as ghosted.
GHOSTED_AFTER_DAYS = 21

# Kept for backwards compatibility with rows written before stages.py existed;
# stages.parse() maps 'new' -> saved and 'no answer' -> ghosted.
STATUS_OPTIONS = ["new", "interested", "applied", "rejected", "no answer"]

# ======================================================
# MATCHING
# ======================================================
TITLE_MATCH_WEIGHT = 3.0  # a skill found in the job title
BODY_MATCH_WEIGHT = 1.0  # a skill found only in the teaser/description
MAX_PLAUSIBLE_YEARS = (
    20  # ignore "years" numbers above this (e.g. "25 years in business")
)

# Scores are normalised against a REALISTIC strong match, not against every
# skill in your list appearing in the job title — no advertisement contains
# thirty-plus skills in its title, so that ceiling is unreachable and squashes
# every score into a narrow band near zero.
#
# A job that hits this many of your skills scores 100. Raise it if too many
# jobs sit at 100; lower it if nothing breaks 50.
#
# Calibrated with `python main.py --calibrate` against 226 stored jobs on
# 2026-07-23: the strongest advertisement lands at 83, the top 10% at 50+, and
# the median at 8 — which is honest, because the median job in a keyword search
# really does share only one skill with the resume.
#
# IMPORTANT: this value depends on how much text is scored. The figure above is
# for teaser-only runs. Scoring full descriptions (--full-desc) finds far more
# matches per job, so re-run --calibrate and expect a higher number if you make
# --full-desc your normal mode.
TARGET_MATCH_SKILLS = 4

# Bumped whenever the scoring formula changes, so stored scores from an older
# formula are never silently compared against new ones.
SCORE_SCALE_VERSION = 2

# Below this many stored jobs, --calibrate refuses to suggest a value —
# a percentile drawn from a handful of rows is noise dressed as a statistic.
CALIBRATION_MIN_JOBS = 150

# Alternate spellings for skills.txt entries. A skill counts as matched if
# the skill itself OR any alias appears (whole-word) in the text.
#
# Keys must match skills.txt lines EXACTLY — the lookup is a plain dict get
# with no normalisation, so renaming a line in skills.txt without renaming the
# key here silently disables alias matching for it. Matching is already
# case-insensitive, so an alias that differs only in case does nothing.
#
# Two groups of keys live here: the ones matching current skills.txt lines
# (used for scoring) and the ones matching MASTER_SKILLS entries (used by
# --generate-skills). Both are needed; they are not duplicates.
SKILL_ALIASES = {
    # --- current skills.txt lines -----------------------------------------
    "Python": ["Python3", "Python 3"],
    "JavaScript ES6": ["JavaScript", "ES6", "ECMAScript"],
    "HTML 5": ["HTML5", "HTML"],
    "CSS 3": ["CSS3", "CSS"],
    "React.js": ["ReactJS", "React JS", "React"],
    "Next.js": ["NextJS", "Next JS"],
    "Tailwind CSS": ["Tailwind", "TailwindCSS"],
    "Bootstrap 5": ["Bootstrap"],
    "Sass": ["SCSS"],
    "Responsive Web Design": ["Responsive Design", "Responsive Website",
                              "Mobile Responsive"],
    "Node.js": ["NodeJS", "Node JS", "Node"],
    "Express.js": ["ExpressJS", "Express JS", "Express"],
    "REST API development": ["REST API", "REST APIs", "RESTful API",
                             "RESTful", "RESTful APIs"],
    "API integration": ["API Integrations", "third-party APIs",
                        "third party APIs"],
    "Authentication and Security": ["Authentication", "Authorization",
                                    "OAuth", "JWT"],
    "MongoDB": ["Mongo"],
    "PostgreSQL": ["Postgres"],
    "Python scripting": ["scripting"],
    "Data extraction": ["Web Scraping", "Web Scraper", "Scraping",
                        "Data Scraping"],
    "Process automation": ["Automation", "Workflow Automation",
                           "Task Automation"],
    "Bash command line": ["Bash", "Shell scripting"],
    # --- MASTER_SKILLS entries (--generate-skills only) -------------------
    "React JS": ["ReactJS", "React.js", "React"],
    "Next JS": ["NextJS", "Next.js"],
    "Node JS": ["NodeJS", "Node.js"],
    "Express JS": ["ExpressJS", "Express.js", "Express"],
    "REST API": ["RESTful API", "REST APIs", "RESTful"],
    "API Integration": ["API Integrations"],
    "Web Scraping": ["Web Scraper", "Scraping"],
    "GitHub (Version Control)": ["GitHub"],
    "Bash Command Line": ["Bash"],
    "Responsive Website": ["Responsive Design", "Responsive Web Design"],
    "Reporting": ["Reports"],
    "Debugging": ["Debug", "Troubleshooting"],
}

# ======================================================
# SKILLS GENERATION (--generate-skills)
# ======================================================
# Canonical skill names scanned against your resume to draft skills.txt.
# Names that also appear in SKILL_ALIASES get alias matching for free.
# Extend freely — only skills actually FOUND in your resume are written.
MASTER_SKILLS = [
    # Languages
    "Python",
    "JavaScript ES6",
    "TypeScript",
    "PHP",
    "Java",
    "C#",
    "C++",
    "Go",
    "Rust",
    "Ruby",
    "Kotlin",
    "Swift",
    "SQL",
    "Bash Command Line",
    "PowerShell",
    "R",
    "Dart",
    # Frontend
    "HTML 5",
    "CSS 3",
    "Sass",
    "Less",
    "Bootstrap 5",
    "Tailwind CSS",
    "jQuery",
    "React JS",
    "Next JS",
    "Vue.js",
    "Nuxt",
    "Angular",
    "Svelte",
    "Redux",
    "Vite",
    "Webpack",
    "Responsive Website",
    "Web Design",
    "UI/UX",
    "Figma",
    # Backend / frameworks
    "Node JS",
    "Express JS",
    "NestJS",
    "Django",
    "Flask",
    "FastAPI",
    "Laravel",
    "CodeIgniter",
    "Spring Boot",
    "ASP.NET",
    "Ruby on Rails",
    "GraphQL",
    "REST API",
    "API Integration",
    "WebSocket",
    "Microservices",
    # Databases
    "MySQL",
    "PostgreSQL",
    "MongoDB",
    "SQLite",
    "Redis",
    "MariaDB",
    "SQL Server",
    "Oracle",
    "Firebase",
    "Supabase",
    "Elasticsearch",
    "DynamoDB",
    "Prisma",
    "Mongoose",
    "Sequelize",
    "SQLAlchemy",
    # Automation / scraping / testing
    "Playwright",
    "Selenium",
    "Puppeteer",
    "BeautifulSoup",
    "Scrapy",
    "Web Scraping",
    "Automation",
    "Pytest",
    "Jest",
    "Cypress",
    "Postman",
    "n8n",
    "Zapier",
    "Make.com",
    "UiPath",
    "Power Automate",
    # Data / AI
    "Pandas",
    "NumPy",
    "Excel",
    "Power BI",
    "Tableau",
    "ETL",
    "Data Analysis",
    "Machine Learning",
    "TensorFlow",
    "PyTorch",
    "OpenAI API",
    "LangChain",
    "Reporting",
    # Cloud / DevOps
    "AWS",
    "Azure",
    "Google Cloud",
    "Docker",
    "Kubernetes",
    "Linux",
    "Nginx",
    "Apache",
    "CI/CD",
    "Jenkins",
    "GitHub Actions",
    "Terraform",
    "Vercel",
    "Netlify",
    "Heroku",
    "DigitalOcean",
    # Tools / practices
    "Git",
    "GitHub (Version Control)",
    "GitLab",
    "Bitbucket",
    "NPM",
    "Yarn",
    "Jira",
    "Trello",
    "Agile",
    "Scrum",
    "WordPress",
    "Shopify",
    "Stripe",
    "Strapi",
    "Authentication",
    "Security",
    "OAuth",
    "JWT",
    "Unit Testing",
    "Debugging",
    # Communication / office
    "Microsoft Office",
    "Google Sheets",
    "Outlook",
    "Slack",
]

# ======================================================
# EMAIL DIGEST (Gmail SMTP)
# ======================================================
# Credentials (GMAIL_ADDRESS, GMAIL_APP_PASSWORD, EMAIL_RECIPIENT) live in
# .env — see .env.example. Only non-secret settings belong here.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS
EMAIL_SUBJECT_PREFIX = "[Job Matcher]"
EMAIL_MAX_ROWS = 30  # cap digest length; full list is always in the CSV/HTML

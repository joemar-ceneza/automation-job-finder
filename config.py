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
DB_PATH = os.path.join(OUTPUT_DIR, "jobs.db")
DEFAULT_SKILLS_FILE = os.path.join(BASE_DIR, "skills.txt")
DEFAULT_SKILLS_DRAFT = os.path.join(BASE_DIR, "skills_draft.txt")
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
DEFAULT_SITES = ["jobstreet", "onlinejobs", "indeed", "remoteok", "remotive"]

JOBSTREET_BASE_URL = "https://ph.jobstreet.com"
ONLINEJOBS_BASE_URL = "https://www.onlinejobs.ph"
INDEED_BASE_URL = "https://ph.indeed.com"
REMOTEOK_API_URL = "https://remoteok.com/api"
REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
API_TIMEOUT_SECONDS = 30

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
    "indeed": {
        "job_card": "div.job_seen_beacon",
        "job_title": "a.jcs-JobTitle span[title]",
        "job_title_link": "a.jcs-JobTitle",
        "job_company": "span[data-testid='company-name']",
        "job_location": "div[data-testid='text-location']",
        "job_teaser": "div[data-testid='belowJobSnippet']",
        "job_salary": "[data-testid*='salary-snippet']",
        "job_detail_description": "#jobDescriptionText",
        "job_detail_salary": "#salaryInfoAndJobType",
    },
}

# ======================================================
# RETRY
# ======================================================
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
RETRY_BACKOFF = 2.0  # delay doubles after each failed attempt

# ======================================================
# APPLICATION STATUS (dashboard + --set-status)
# ======================================================
STATUS_OPTIONS = ["new", "interested", "applied", "rejected", "no answer"]

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

# ======================================================
# SKILLS GENERATION (--generate-skills)
# ======================================================
# Canonical skill names scanned against your resume to draft skills.txt.
# Names that also appear in SKILL_ALIASES get alias matching for free.
# Extend freely — only skills actually FOUND in your resume are written.
MASTER_SKILLS = [
    # Languages
    "Python", "JavaScript ES6", "TypeScript", "PHP", "Java", "C#", "C++",
    "Go", "Rust", "Ruby", "Kotlin", "Swift", "SQL", "Bash Command Line",
    "PowerShell", "R", "Dart",
    # Frontend
    "HTML 5", "CSS 3", "Sass", "Less", "Bootstrap 5", "Tailwind CSS",
    "jQuery", "React JS", "Next JS", "Vue.js", "Nuxt", "Angular", "Svelte",
    "Redux", "Vite", "Webpack", "Responsive Website", "Web Design",
    "UI/UX", "Figma",
    # Backend / frameworks
    "Node JS", "Express JS", "NestJS", "Django", "Flask", "FastAPI",
    "Laravel", "CodeIgniter", "Spring Boot", "ASP.NET", "Ruby on Rails",
    "GraphQL", "REST API", "API Integration", "WebSocket", "Microservices",
    # Databases
    "MySQL", "PostgreSQL", "MongoDB", "SQLite", "Redis", "MariaDB",
    "SQL Server", "Oracle", "Firebase", "Supabase", "Elasticsearch",
    "DynamoDB", "Prisma", "Mongoose", "Sequelize", "SQLAlchemy",
    # Automation / scraping / testing
    "Playwright", "Selenium", "Puppeteer", "BeautifulSoup", "Scrapy",
    "Web Scraping", "Automation", "Pytest", "Jest", "Cypress", "Postman",
    "n8n", "Zapier", "Make.com", "UiPath", "Power Automate",
    # Data / AI
    "Pandas", "NumPy", "Excel", "Power BI", "Tableau", "ETL",
    "Data Analysis", "Machine Learning", "TensorFlow", "PyTorch",
    "OpenAI API", "LangChain", "Reporting",
    # Cloud / DevOps
    "AWS", "Azure", "Google Cloud", "Docker", "Kubernetes", "Linux",
    "Nginx", "Apache", "CI/CD", "Jenkins", "GitHub Actions", "Terraform",
    "Vercel", "Netlify", "Heroku", "DigitalOcean",
    # Tools / practices
    "Git", "GitHub (Version Control)", "GitLab", "Bitbucket", "NPM",
    "Yarn", "Jira", "Trello", "Agile", "Scrum", "WordPress", "Shopify",
    "Stripe", "Strapi", "Authentication", "Security", "OAuth", "JWT",
    "Unit Testing", "Debugging",
    # Communication / office
    "Microsoft Office", "Google Sheets", "Outlook", "Slack",
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

"""
skill_extractor.py
Finds canonical skills in job advertisements and assigns each a category, so
demand can be aggregated with plain SQL instead of a language model.

Extraction is deliberately dictionary-based: it only ever reports a skill from
config.MASTER_SKILLS, which means a count of "Docker" is a count of Docker and
not of something a model decided looked like it.
"""
import logging
import re

import config
from resume_parser import skill_in_text

# Which bucket each skill belongs to on the analytics page. Anything not
# listed falls back to "tool", which keeps a new MASTER_SKILLS entry from
# silently vanishing from the charts.
_CATEGORY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("language", (
        "Python", "JavaScript ES6", "TypeScript", "PHP", "Java", "C#", "C++",
        "Go", "Rust", "Ruby", "Kotlin", "Swift", "SQL", "R", "Dart",
        "Bash Command Line", "Bash command line", "PowerShell",
        "Python scripting",
    )),
    ("framework", (
        "React JS", "React.js", "Next JS", "Next.js", "Vue.js", "Nuxt",
        "Angular", "Svelte", "Redux", "Node JS", "Node.js", "Express JS",
        "Express.js", "NestJS", "Django", "Flask", "FastAPI", "Laravel",
        "CodeIgniter", "Spring Boot", "ASP.NET", "Ruby on Rails", "jQuery",
        "Bootstrap 5", "Tailwind CSS", "Sass", "Less", "HTML 5", "CSS 3",
        "Vite", "Webpack", "Strapi",
    )),
    ("database", (
        "MySQL", "PostgreSQL", "MongoDB", "SQLite", "Redis", "MariaDB",
        "SQL Server", "Oracle", "Firebase", "Supabase", "Elasticsearch",
        "DynamoDB", "Prisma", "Mongoose", "Sequelize", "SQLAlchemy",
        "MongoDB Atlas",
    )),
    ("cloud", (
        "AWS", "Azure", "Google Cloud", "Docker", "Kubernetes", "Linux",
        "Nginx", "Apache", "CI/CD", "Jenkins", "GitHub Actions", "Terraform",
        "Vercel", "Netlify", "Heroku", "DigitalOcean", "GitHub Pages",
        "Microservices",
    )),
    ("ai", (
        "Machine Learning", "TensorFlow", "PyTorch", "OpenAI API", "LangChain",
        "Data Analysis", "Pandas", "NumPy", "ETL",
    )),
]

_CATEGORY_BY_SKILL: dict[str, str] = {
    skill: category
    for category, skills in _CATEGORY_PATTERNS
    for skill in skills
}

_DEFAULT_CATEGORY = "tool"

# Skills whose names are common English words need the extra context of a
# longer phrase before they count, or every advertisement "requires" them.
_AMBIGUOUS = {"Go", "R", "Automation", "Security", "Excel", "Oracle"}
_AMBIGUOUS_CONTEXT = re.compile(
    r"(experience|proficien|knowledge|skill|familiar|using|with|in)\b",
    re.IGNORECASE)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _is_credible(skill: str, title: str, body: str) -> bool:
    """
    Guards single-word skills that double as ordinary English. 'Go' must
    appear near hiring language, not in 'go-getter'.
    """
    if skill not in _AMBIGUOUS:
        return True
    if skill_in_text(skill, title.lower()):
        return True
    for sentence in re.split(r"[.;\n]", body):
        if skill_in_text(skill, sentence.lower()):
            return bool(_AMBIGUOUS_CONTEXT.search(sentence))
    return False


# ======================================================
# PUBLIC API
# ======================================================
def category_for(skill: str) -> str:
    """The analytics bucket a skill belongs to."""
    return _CATEGORY_BY_SKILL.get(skill, _DEFAULT_CATEGORY)


def extract_skills(title: str, body: str) -> list[tuple[str, str, bool]]:
    """
    Finds every MASTER_SKILLS entry present in a job advertisement.
    Returns (skill, category, found_in_title) tuples.
    """
    title_lower = (title or "").lower()
    body_lower = (body or "").lower()
    found = []
    for skill in config.MASTER_SKILLS:
        in_title = skill_in_text(skill, title_lower)
        if not in_title and not skill_in_text(skill, body_lower):
            continue
        if not _is_credible(skill, title or "", body or ""):
            continue
        found.append((skill, category_for(skill), in_title))
    return found


def extract_for_rows(rows: list[dict]) -> list[tuple[str, str, str, int]]:
    """
    Builds job_skills rows for a batch of scored jobs.
    Returns (job_key, skill, category, in_title) tuples ready for insertion.
    """
    extracted = []
    for row in rows:
        text = " ".join(part for part in (row.get("teaser", ""),
                                          row.get("description", "")) if part)
        for skill, category, in_title in extract_skills(row.get("title", ""),
                                                        text):
            extracted.append((row["job_key"], skill, category, int(in_title)))
    logging.info("Extracted %d skill mentions from %d jobs.",
                 len(extracted), len(rows))
    return extracted

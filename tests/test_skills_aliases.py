"""
Tests that skills.txt and config.SKILL_ALIASES stay in agreement.

The bug these guard against: SKILL_ALIASES keys are looked up with a plain
dict get, so renaming a line in skills.txt (React JS -> React.js) silently
disables alias matching for it, with no error and no visible symptom beyond
scores quietly getting worse.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import resume_parser

SKILLS_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "skills.txt")

# Lines that genuinely need no alternate spelling.
NO_ALIAS_NEEDED = {
    "typescript", "php", "sql", "jquery", "mysql", "mongoose", "prisma",
    "playwright", "git", "github", "npm", "strapi", "stripe", "netlify",
    "github pages", "mongodb atlas",
}


@pytest.fixture(scope="module")
def skills() -> list[str]:
    return resume_parser.load_skills(SKILLS_FILE)


def test_skills_file_loads(skills):
    assert skills, "skills.txt produced no entries"


def test_every_multiword_skill_has_aliases_or_is_exempt(skills):
    """A skill whose exact phrasing rarely appears in ads needs aliases."""
    lowered = {key.lower(): key for key in config.SKILL_ALIASES}
    missing = [
        skill for skill in skills
        if skill.lower() not in NO_ALIAS_NEEDED
        and skill.lower() not in lowered
    ]
    assert not missing, (
        "these skills.txt lines have no SKILL_ALIASES entry and no exemption, "
        f"so only their literal text will ever match: {missing}"
    )


def test_alias_lookup_is_exact_not_fuzzy(skills):
    """Document the sharp edge: the lookup is case-sensitive on the key."""
    for skill in skills:
        aliases = config.SKILL_ALIASES.get(skill, [])
        if aliases:
            assert skill in config.SKILL_ALIASES, (
                f"{skill!r} resolved aliases, so its key must match exactly")


def test_aliases_are_not_case_only_variants():
    """Matching already lowercases, so a case-only alias is dead weight."""
    for key, aliases in config.SKILL_ALIASES.items():
        seen = {key.lower()}
        for alias in aliases:
            assert alias.lower() not in seen, (
                f"{key!r} lists {alias!r}, which differs only by case from an "
                "entry already present — matching is case-insensitive"
            )
            seen.add(alias.lower())


def test_skills_ending_in_symbols_match():
    """
    Regression: \\b after '+' or '#' is never a boundary, so 'C++' and 'C#'
    silently matched nothing at all while every other skill worked.
    """
    assert resume_parser.skill_in_text("C++", "c++ developer wanted")
    assert resume_parser.skill_in_text("C#", "strong c# and asp.net skills")
    assert resume_parser.skill_in_text("ASP.NET", "asp.net core experience")
    # ...without matching a longer word that merely starts the same way
    assert not resume_parser.skill_in_text("Go", "golang developer wanted")
    assert not resume_parser.skill_in_text("C#", "c#sharp-ish nonsense")


def test_renamed_skills_actually_match_job_text(skills):
    """The regression itself: React.js must match an ad that says 'ReactJS'."""
    cases = [
        ("React.js", "Looking for a ReactJS developer"),
        ("React.js", "Strong React experience required"),
        ("Node.js", "Backend work in NodeJS"),
        ("Express.js", "Built with Express and MongoDB"),
        ("REST API development", "Design and consume REST APIs"),
        ("Data extraction", "Web scraping and data pipelines"),
        ("Process automation", "Automation of manual workflows"),
        ("Bash command line", "Comfortable with Bash"),
        ("Responsive Web Design", "Responsive design across devices"),
        ("Authentication and Security", "Implement OAuth and JWT flows"),
    ]
    for skill, text in cases:
        assert skill in skills, f"{skill!r} is not in skills.txt any more"
        assert resume_parser.skill_in_text(skill, text.lower()), (
            f"{skill!r} failed to match {text!r} — its aliases are broken"
        )

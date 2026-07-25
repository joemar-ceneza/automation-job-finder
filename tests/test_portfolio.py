"""
Tests for portfolio matching.

Two things matter: loading never raises on a file you hand-edited badly (a
broken side-feature must not stop you looking at a job), and ranking uses the
job scorer rather than a second algorithm — a tag in the job title outranks one
buried in the body, exactly as it does when scoring a resume.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio

PORTFOLIO_TOML = """
[[project]]
name = "Job Finder"
url = "https://example.com/job-finder"
tech = ["Python", "Playwright", "PostgreSQL"]
summary = "Scrapes and scores job ads."
highlights = ["353 tests"]

[[project]]
name = "Shop Front"
url = "https://example.com/shop"
tech = ["React.js", "Node.js"]
summary = "An online store."
"""


def write(tmp_path, text: str) -> str:
    path = tmp_path / "portfolio.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def job(title="Python Developer", description="Playwright and PostgreSQL.") -> dict:
    return {"job_key": "jobstreet:id:1", "title": title,
            "description": description}


# ======================================================
# LOADING
# ======================================================
def test_projects_load_from_toml(tmp_path):
    projects = portfolio.load(write(tmp_path, PORTFOLIO_TOML))
    assert [project.name for project in projects] == ["Job Finder", "Shop Front"]
    assert projects[0].tech == ["Python", "Playwright", "PostgreSQL"]
    assert projects[0].highlights == ["353 tests"]


def test_a_missing_file_is_an_empty_portfolio(tmp_path):
    assert portfolio.load(str(tmp_path / "nope.toml")) == []


def test_a_malformed_file_does_not_raise(tmp_path):
    projects = portfolio.load(write(tmp_path, "[[project]\nname = broken"))
    assert projects == []


def test_an_entry_without_a_name_is_skipped(tmp_path):
    text = '[[project]]\nurl = "https://example.com"\n\n' + PORTFOLIO_TOML
    projects = portfolio.load(write(tmp_path, text))
    assert all(project.name for project in projects)
    assert len(projects) == 2


def test_a_multiline_summary_is_collapsed(tmp_path):
    projects = portfolio.load(write(tmp_path, PORTFOLIO_TOML))
    assert "\n" not in projects[0].summary


# ======================================================
# MATCHING
# ======================================================
def _projects(tmp_path):
    return portfolio.load(write(tmp_path, PORTFOLIO_TOML))


def test_the_most_relevant_project_ranks_first(tmp_path):
    result = portfolio.match_job(job(), _projects(tmp_path))
    assert result.best.project.name == "Job Finder"
    assert result.best.score_percent > 0


def test_a_different_job_promotes_a_different_project(tmp_path):
    frontend = job(title="React Developer", description="Node.js and React.")
    result = portfolio.match_job(frontend, _projects(tmp_path))
    assert result.best.project.name == "Shop Front"


def test_matched_and_unmatched_tags_are_reported(tmp_path):
    result = portfolio.match_job(job(), _projects(tmp_path))
    best = result.best
    assert "Python" in best.matched
    assert "Playwright" in best.matched
    assert all(tag not in best.matched for tag in best.unmatched)


def test_a_tag_in_the_job_title_is_recorded_as_such(tmp_path):
    """The scorer weighs title hits triple — the match must record which."""
    result = portfolio.match_job(job(), _projects(tmp_path))
    assert "Python" in result.best.title_matches


def test_an_irrelevant_job_scores_zero_and_says_so(tmp_path):
    result = portfolio.match_job(job(title="Pastry Chef", description="Baking."),
                                 _projects(tmp_path))
    assert result.best.score_percent == 0.0
    assert any("transferable" in line for line in result.lines)


def test_an_empty_portfolio_is_handled(tmp_path):
    result = portfolio.match_job(job(), [])
    assert result.matches == []
    assert result.best is None
    assert any("portfolio.toml" in line for line in result.lines)


def test_the_shipped_portfolio_file_parses():
    """The starter file must be valid — it is the template people copy."""
    projects = portfolio.load()
    assert projects, "the shipped data/portfolio.toml should parse"
    assert all(project.tech for project in projects)

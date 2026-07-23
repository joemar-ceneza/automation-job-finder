"""Tests for the resume registry and side-by-side comparison."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

FULLSTACK = """# Jane Dev
Full Stack Developer
jane@example.com · +63 900 000 0000

## Skills

Python, React.js, HTML 5, CSS 3, PostgreSQL

## Experience

### Developer — Acme
2021 - Present
- Built React interfaces and Django APIs, cutting load time by 30%.
"""

BACKEND = """# Jane Dev
Backend Developer
jane@example.com · +63 900 000 0000

## Skills

Python, PostgreSQL, Docker

## Experience

### Developer — Acme
2021 - Present
- Built Django APIs on PostgreSQL, cutting query time by 30%.
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """An isolated resumes/ folder and database."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "out" / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "out" / "backups"))
    monkeypatch.setattr(config, "RESUMES_DIR", str(tmp_path / "resumes"))
    monkeypatch.setattr(config, "MASTER_RESUME_FILE",
                        str(tmp_path / "master_resume.md"))
    os.makedirs(config.RESUMES_DIR, exist_ok=True)
    (tmp_path / "resumes" / "main.md").write_text(FULLSTACK, encoding="utf-8")
    (tmp_path / "resumes" / "backend.md").write_text(BACKEND, encoding="utf-8")

    import db_handler
    db_handler.init_db()
    import resumes
    return resumes


# ======================================================
# REGISTRY
# ======================================================
def test_resumes_are_found_by_filename(registry):
    assert registry.names() == ["backend", "main"]


def test_a_resume_loads_its_content(registry):
    resume = registry.get("main").load()
    assert "React.js" in resume.listed_skills()


def test_unknown_names_return_none(registry):
    assert registry.get("nonexistent") is None


def test_the_conventional_name_is_the_default(registry):
    assert registry.default_name() == config.DEFAULT_RESUME_NAME


def test_the_default_can_be_changed(registry):
    assert registry.set_default("backend")
    assert registry.default_name() == "backend"


def test_setting_an_unknown_default_is_refused(registry):
    assert registry.set_default("nope") is False
    assert registry.default_name() == config.DEFAULT_RESUME_NAME


def test_resolve_falls_back_to_the_default(registry):
    assert registry.resolve(None).name == config.DEFAULT_RESUME_NAME
    assert registry.resolve("backend").name == "backend"
    assert registry.resolve("nope") is None


def test_a_legacy_master_resume_is_migrated(tmp_path, monkeypatch):
    """The pre-multi-resume file is copied in, not silently ignored."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "out" / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "out" / "bak"))
    monkeypatch.setattr(config, "RESUMES_DIR", str(tmp_path / "resumes"))
    legacy = tmp_path / "master_resume.md"
    legacy.write_text(FULLSTACK, encoding="utf-8")
    monkeypatch.setattr(config, "MASTER_RESUME_FILE", str(legacy))

    import db_handler
    db_handler.init_db()
    import resumes

    assert resumes.names() == [config.DEFAULT_RESUME_NAME]
    assert legacy.exists(), "the original must not be destroyed"


# ======================================================
# COMPARISON
# ======================================================
def job(title: str, description: str) -> dict:
    return {"job_key": "jobstreet:id:1", "title": title,
            "description": description, "company": "Acme"}


def rank(registry, spec: dict):
    import optimizer
    return optimizer.compare(
        spec, [(ref.name, ref.load()) for ref in registry.available()])


def test_the_frontend_resume_wins_a_frontend_job(registry):
    rankings = rank(registry, job(
        "Frontend Developer",
        "React, HTML5 and CSS3 work building responsive interfaces."))
    assert rankings[0].name == "main"
    assert rankings[0].combined > rankings[1].combined


def test_both_resumes_tie_when_nothing_separates_them(registry):
    """A tie is information, not a failure — neither is better here."""
    rankings = rank(registry, job("Python Developer",
                                  "Python and PostgreSQL only."))
    assert rankings[0].combined == rankings[1].combined


def test_the_backend_resume_wins_when_its_extra_skill_is_asked_for(registry):
    rankings = rank(registry, job("Platform Engineer",
                                  "Python, PostgreSQL and Docker."))
    assert rankings[0].name == "backend"


def test_missing_skills_are_reported_per_resume(registry):
    rankings = rank(registry, job("Frontend Developer", "React and CSS3."))
    backend = next(r for r in rankings if r.name == "backend")
    assert "CSS 3" in backend.missing


def test_ranking_is_ordered_best_first(registry):
    rankings = rank(registry, job("Frontend Developer", "React and HTML5."))
    scores = [ranking.combined for ranking in rankings]
    assert scores == sorted(scores, reverse=True)


def test_every_resume_is_ranked(registry):
    rankings = rank(registry, job("Developer", "Python."))
    assert {ranking.name for ranking in rankings} == {"main", "backend"}


def test_a_job_naming_no_skills_does_not_crash(registry):
    rankings = rank(registry, job("Pastry Chef", "Baking bread."))
    assert len(rankings) == 2
    assert all(ranking.match_percent == 0.0 for ranking in rankings)

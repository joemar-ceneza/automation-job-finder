"""
Tests for Feature 16 — skill-merge proposals and the tracked-skills store.

The engine must surface only skills the corpus genuinely asks for that aren't
already tracked, never re-propose one it already knows under any surface form,
and count honestly. The store must persist an approval and hand it back in the
shape the extractor consumes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import skill_extractor
import skill_proposals


def jobs(*descriptions: str) -> list[dict]:
    return [{"job_key": f"j{i}", "title": "Developer", "description": text}
            for i, text in enumerate(descriptions)]


# ======================================================
# PROPOSAL ENGINE
# ======================================================
def test_an_untracked_in_demand_skill_is_proposed():
    # Symfony is in the lexicon but not in MASTER_SKILLS.
    corpus = jobs("We use Symfony daily.", "Strong Symfony experience.",
                  "PHP and Symfony.")
    names = [p.canonical for p in skill_proposals.propose(corpus, 3)]
    assert "Symfony" in names


def test_below_the_threshold_is_not_proposed():
    corpus = jobs("We use Symfony.", "Unrelated posting.")
    names = [p.canonical for p in skill_proposals.propose(corpus, 3)]
    assert "Symfony" not in names


def test_a_tracked_skill_is_never_proposed():
    # Docker is in MASTER_SKILLS — it must never be suggested, however common.
    corpus = jobs("Docker required.", "Docker and containers.",
                  "Docker experience a must.")
    names = [p.canonical for p in skill_proposals.propose(corpus, 1)]
    assert "Docker" not in names


def test_variants_are_counted_and_reported():
    corpus = jobs("Apache Kafka pipeline.", "We run Kafka.", "Kafka streams.")
    result = skill_proposals.propose(corpus, 3)
    kafka = next(p for p in result if p.canonical == "Kafka")
    assert kafka.occurrences == 3
    assert any("kafka" in form.lower() for form in kafka.merge_from)


def test_an_approved_extra_is_not_re_proposed():
    corpus = jobs("Symfony.", "Symfony.", "Symfony.")
    names = [p.canonical for p in skill_proposals.propose(
        corpus, 1, extra_tracked=[("Symfony", "framework", ())])]
    assert "Symfony" not in names


def test_proposals_are_ordered_by_demand():
    corpus = jobs("Symfony", "Symfony", "Symfony", "Flutter", "Flutter")
    names = [p.canonical for p in skill_proposals.propose(corpus, 2)]
    assert names.index("Symfony") < names.index("Flutter")


# ======================================================
# EXTRACTOR HONOURS APPROVED EXTRAS
# ======================================================
def test_the_extractor_finds_an_approved_extra_skill():
    extra = [("Symfony", "framework", ())]
    found = skill_extractor.extract_skills(
        "Backend Developer", "We build on Symfony and PHP.", extra)
    names = [skill for skill, _c, _t in found]
    assert "Symfony" in names


def test_an_approved_extra_matches_by_alias():
    extra = [("Kafka", "cloud", ("Apache Kafka",))]
    found = skill_extractor.extract_skills(
        "Data Engineer", "Experience with Apache Kafka required.", extra)
    assert any(skill == "Kafka" for skill, _c, _t in found)


def test_extra_skills_default_to_empty():
    """Calling without extras is unchanged behaviour."""
    found = skill_extractor.extract_skills("Dev", "Python and Docker.")
    names = [skill for skill, _c, _t in found]
    assert "Python" in names and "Docker" in names


# ======================================================
# TRACKED-SKILLS STORE (needs a throwaway database)
# ======================================================
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    import db_handler
    db_handler.init_db()
    return db_handler


def test_an_approval_is_persisted_and_returned_in_extractor_shape(db):
    import tracked_skills
    assert tracked_skills.add("Symfony", "framework", ("Sf",)) is True
    additions = tracked_skills.additions()
    assert ("Symfony", "framework", ("Sf",)) in additions


def test_adding_the_same_skill_twice_is_idempotent(db):
    import tracked_skills
    assert tracked_skills.add("Flutter", "framework") is True
    assert tracked_skills.add("Flutter", "framework") is False
    assert len(tracked_skills.additions()) == 1


def test_a_tracked_skill_can_be_removed(db):
    import tracked_skills
    tracked_skills.add("Blender", "tool")
    assert tracked_skills.remove("Blender") is True
    assert tracked_skills.additions() == []
    assert tracked_skills.remove("Blender") is False


def test_no_additions_by_default(db):
    import tracked_skills
    assert tracked_skills.additions() == []


# ======================================================
# SKILL CO-OCCURRENCE (a self-join over job_skills)
# ======================================================
def _job_row(job_key: str) -> dict:
    return {"job_key": job_key, "title": "Developer", "company": "Acme",
            "location": "Manila", "url": "https://example.com",
            "source": "jobstreet", "salary": "", "salary_min": "",
            "salary_max": "", "work_arrangement": "", "listing_date": "",
            "status": "saved", "search_keyword": "python",
            "score_percent": 10.0, "matched_skills": "", "required_years": "",
            "description": ""}


def test_cooccurrence_counts_what_appears_alongside(db):
    db.insert_jobs([_job_row("j1"), _job_row("j2"), _job_row("j3")])
    db.replace_job_skills([
        ("j1", "Docker", "cloud", 0), ("j1", "AWS", "cloud", 0),
        ("j2", "Docker", "cloud", 0), ("j2", "AWS", "cloud", 0),
        ("j3", "Docker", "cloud", 0), ("j3", "Python", "language", 0),
    ])
    result = db.skill_cooccurrence("Docker", limit=5)
    assert result["base"] == 3
    together = {row["skill"]: row["together"] for row in result["rows"]}
    assert together["AWS"] == 2
    assert together["Python"] == 1


def test_cooccurrence_never_pairs_a_skill_with_itself(db):
    db.insert_jobs([_job_row("j1")])
    db.replace_job_skills([("j1", "Docker", "cloud", 0),
                           ("j1", "AWS", "cloud", 0)])
    result = db.skill_cooccurrence("Docker", limit=5)
    assert "Docker" not in [row["skill"] for row in result["rows"]]


def test_cooccurrence_of_an_unknown_skill_is_empty(db):
    db.insert_jobs([_job_row("j1")])
    db.replace_job_skills([("j1", "Docker", "cloud", 0)])
    result = db.skill_cooccurrence("Kubernetes", limit=5)
    assert result["base"] == 0 and result["rows"] == []

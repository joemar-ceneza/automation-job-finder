"""
Tests for the master resume model.

The load-bearing property is the round trip: parse(render(x)) == x. Everything
downstream — reordering, bullet rewriting, export — mutates the model and
writes it back, so a lossy round trip would quietly corrupt the resume.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import resume_model
from resume_model import Contact, Entry, MasterResume, Section

SAMPLE = """# Joemar Ceneza
Full Stack Developer
joemar@example.com · +63 917 000 0000 · Manila · github.com/joemar

## Summary

Full stack developer building automation tools in Python and React.

## Skills

Python, React.js, MongoDB, Playwright

## Experience

### Software Developer — Acme Corp
Manila · Jan 2023 - Present
- Built a scraping pipeline in Python that cut manual work by 30%.
- Automated reporting with Playwright.

### Junior Developer — Beta Inc
Remote · Jun 2021 - Dec 2022
- Maintained a React.js dashboard.

## Education

### BS Computer Science — State University
2017 - 2021
"""


@pytest.fixture
def resume() -> MasterResume:
    return resume_model.parse_markdown(SAMPLE)


# ======================================================
# PARSING
# ======================================================
def test_contact_is_read(resume):
    assert resume.contact.name == "Joemar Ceneza"
    assert resume.contact.headline == "Full Stack Developer"
    assert resume.contact.email == "joemar@example.com"
    assert "917" in resume.contact.phone
    assert resume.contact.location == "Manila"
    assert "github.com/joemar" in resume.contact.links


def test_sections_are_found_in_order(resume):
    assert [section.name for section in resume.sections] == [
        "Summary", "Skills", "Experience", "Education"]


def test_prose_section_keeps_its_text(resume):
    summary = resume.section("Summary")
    assert summary.kind == "prose"
    assert "automation tools" in summary.prose
    assert summary.entries == []


def test_skills_section_becomes_a_list(resume):
    skills = resume.section("Skills")
    assert skills.kind == "list"
    assert skills.items == ["Python", "React.js", "MongoDB", "Playwright"]


def test_experience_entries_are_structured(resume):
    experience = resume.section("Experience")
    assert len(experience.entries) == 2
    first = experience.entries[0]
    assert first.title == "Software Developer"
    assert first.organisation == "Acme Corp"
    assert first.meta == "Manila · Jan 2023 - Present"
    assert len(first.bullets) == 2
    assert first.bullets[0].startswith("Built a scraping pipeline")


def test_section_lookup_is_case_insensitive(resume):
    assert resume.section("experience") is not None
    assert resume.section("EXPERIENCE") is not None
    assert resume.section("nope") is None


# ======================================================
# THE ROUND TRIP
# ======================================================
def test_round_trip_is_lossless(resume):
    reparsed = resume_model.parse_markdown(resume.to_markdown())
    assert reparsed == resume


def test_round_trip_is_stable_on_a_second_pass(resume):
    once = resume.to_markdown()
    twice = resume_model.parse_markdown(once).to_markdown()
    assert once == twice


def test_round_trip_survives_an_edit(resume):
    """Rewriting a bullet is what AI mode does — it must not corrupt anything."""
    resume.section("Experience").entries[0].bullets[0] = "Rewritten bullet."
    reparsed = resume_model.parse_markdown(resume.to_markdown())
    assert reparsed.section("Experience").entries[0].bullets[0] == \
           "Rewritten bullet."
    assert len(reparsed.section("Experience").entries) == 2


# ======================================================
# QUERIES
# ======================================================
def test_listed_skills_come_from_the_skills_section(resume):
    assert resume.listed_skills() == ["Python", "React.js", "MongoDB",
                                      "Playwright"]


def test_all_bullets_spans_every_section(resume):
    assert len(resume.all_bullets()) == 3


def test_full_text_includes_name_and_bullets(resume):
    text = resume.full_text()
    assert "Joemar Ceneza" in text
    assert "Playwright" in text
    assert "State University" in text


# ======================================================
# TRANSFORMS
# ======================================================
def test_reordering_moves_named_sections_first(resume):
    reordered = resume.reordered(["Skills", "Experience"])
    assert [s.name for s in reordered.sections][:2] == ["Skills", "Experience"]


def test_reordering_never_drops_a_section(resume):
    reordered = resume.reordered(["Skills"])
    assert {s.name for s in reordered.sections} == \
           {s.name for s in resume.sections}


def test_reordering_ignores_unknown_names(resume):
    reordered = resume.reordered(["Nonexistent", "Skills"])
    assert reordered.sections[0].name == "Skills"


# ======================================================
# EDGE CASES
# ======================================================
def test_empty_document_is_safe():
    empty = resume_model.parse_markdown("")
    assert empty.sections == []
    assert empty.contact.name == ""
    assert empty.to_markdown().strip() == "#"


def test_entry_heading_accepts_different_dashes():
    for dash in ("—", "–", "-"):
        parsed = resume_model.parse_markdown(
            f"# N\n\n## Experience\n\n### Dev {dash} Acme\n- Did work.\n")
        entry = parsed.section("Experience").entries[0]
        assert (entry.title, entry.organisation) == ("Dev", "Acme"), dash


def test_entry_without_an_organisation_still_parses():
    parsed = resume_model.parse_markdown(
        "# N\n\n## Projects\n\n### Job Finder\n- Scrapes job ads.\n")
    entry = parsed.section("Projects").entries[0]
    assert entry.title == "Job Finder"
    assert entry.organisation == ""
    assert entry.heading() == "Job Finder"


def test_save_and_load_round_trip(tmp_path, resume):
    path = tmp_path / "nested" / "master_resume.md"
    resume_model.save(resume, str(path))
    assert resume_model.load(str(path)) == resume

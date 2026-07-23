"""
Tests for bootstrapping a master resume from extracted PDF text.

Two regressions are pinned here, both found by importing a real resume:
a PDF wraps long bullets across lines with no marker on the continuation, and
resumes group skills under category labels that must not fuse onto a skill.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import resume_import
import resume_model

WRAPPED_PDF_TEXT = """JOEMAR CENEZA
Quezon City, Metro Manila, Philippines
joemar.ceneza@gmail.com | +63 976 056 1763

SKILLS
Languages: Python, JavaScript (ES6+), TypeScript, SQL
Front-End Development: React.js, Next.js, Tailwind CSS
Databases and ORMs: MongoDB, PostgreSQL, Prisma

EXPERIENCE
Account Compliance Specialist (Python Automation)
Leekie Enterprise Inc. | November 2024 - Present
- Develop and maintain Python-based automation tools that streamline
compliance monitoring, reducing manual review time for the team.
- Build browser automation workflows with Playwright to collect and
validate operational data.

Account Administrator
Leekie Enterprise Inc. | May 2016 - November 2024
- Audited and verified data entries to maintain data integrity,
identifying discrepancies and implementing corrections.

EDUCATION
Bachelor of Science in Information Technology
STI College Munoz - EDSA | 2011 - 2015
"""


# ======================================================
# WRAPPED BULLETS
# ======================================================
def test_wrapped_bullets_are_rejoined():
    """
    The regression: an unmarked continuation line became its own bullet,
    cutting every long achievement in half mid-sentence.
    """
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    bullets = resume.section("Experience").entries[0].bullets
    assert len(bullets) == 2, f"expected 2 whole bullets, got {bullets}"
    assert bullets[0].endswith("manual review time for the team.")
    assert "streamline compliance monitoring" in bullets[0]


def test_no_bullet_starts_mid_sentence():
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    for bullet in resume.all_bullets():
        assert bullet[0].isupper(), f"bullet starts mid-sentence: {bullet!r}"


# ======================================================
# SKILL CATEGORIES
# ======================================================
def test_category_labels_are_stripped_from_skills():
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    skills = resume.section("Skills").items
    assert "Python" in skills
    assert not any(skill.startswith("Languages:") for skill in skills)
    assert not any(":" in skill for skill in skills), skills


def test_categories_do_not_fuse_across_lines():
    """'SQL' and 'Front-End Development: React.js' must not merge."""
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    skills = resume.section("Skills").items
    assert "SQL" in skills
    assert "React.js" in skills
    assert not any("Front-End" in skill for skill in skills)


# ======================================================
# STRUCTURE
# ======================================================
def test_sections_are_recognised_from_all_caps_headings():
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    assert [section.name for section in resume.sections] == [
        "Skills", "Experience", "Education"]


def test_employers_and_dates_are_separated():
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    entries = resume.section("Experience").entries
    assert len(entries) == 2
    assert entries[0].title.startswith("Account Compliance Specialist")
    assert "November 2024" in entries[0].meta
    assert entries[1].title == "Account Administrator"


def test_contact_details_are_read():
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    assert resume.contact.name == "JOEMAR CENEZA"
    assert resume.contact.email == "joemar.ceneza@gmail.com"
    assert "976" in resume.contact.phone


def test_imported_resume_round_trips():
    """The import must produce a document the model can re-read exactly."""
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    assert resume_model.parse_markdown(resume.to_markdown()) == resume


def test_empty_input_is_safe():
    resume = resume_import.from_resume_text("")
    assert resume.sections == []


# ======================================================
# DRAFT FILE
# ======================================================
def test_draft_banner_is_a_comment_the_parser_ignores(tmp_path):
    resume = resume_import.from_resume_text(WRAPPED_PDF_TEXT)
    path = tmp_path / "master_resume.md"
    resume_import.write_draft(resume, str(path))

    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!--")
    assert "source of truth" in text
    # Re-reading must ignore the banner entirely.
    assert resume_model.load(str(path)) == resume

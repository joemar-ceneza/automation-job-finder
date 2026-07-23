"""
Tests for Standard-mode cover letters.

Most of these pin bugs found by generating a letter from the real resume
rather than a fixture — each one produced output that could not have been
sent to an employer.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cover_letter
import resume_model

RESUME = """# Jane Dev
Full Stack Developer
jane@example.com · +63 900 000 0000 · Manila

## Professional Summary

Full stack developer with Python and React.js experience.

## Technical Skills

Python, React.js, PostgreSQL, Playwright

## Experience

### Support Engineer — Helpdesk Co
2019 - 2021
- Answered tickets and escalated issues.

### Backend Developer — Acme Corp
2021 - Present
- Built REST APIs in Django backed by PostgreSQL.
- Automated reporting with Playwright, cutting effort by 40%.

## Education

### BS Computer Science — State University
2015 - 2019
- Graduated with Python and React.js coursework.
"""


@pytest.fixture
def resume():
    return resume_model.parse_markdown(RESUME)


def job(**overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Developer",
            "company": "Globe Telecom",
            "description": "Django, PostgreSQL and Playwright work."}
    return {**base, **overrides}


# ======================================================
# SECTION CLASSIFICATION (the worst bug found)
# ======================================================
def test_professional_summary_is_prose_not_a_job(resume):
    """
    Regression: only the exact name "summary" counted as prose, so
    "Professional Summary" was parsed as a job. Because it mentioned Python
    and React.js it then outranked the real roles, and the letter quoted the
    summary paragraph as the candidate's employer.
    """
    assert resume.section("Professional Summary").kind == "prose"
    assert resume.section("Professional Summary").entries == []


def test_technical_skills_is_a_list(resume):
    """Same exact-match flaw: "Technical Skills" must still be a list."""
    section = resume.section("Technical Skills")
    assert section.kind == "list"
    assert "Python" in section.items


def test_only_experience_sections_supply_the_role(resume):
    """A degree is not a job, and must never be quoted as an employer."""
    letter = cover_letter.compose(resume, job())
    text = letter.to_text()
    assert "Acme Corp" in text
    assert "State University" not in text
    assert "Graduated with" not in text


# ======================================================
# CONTENT SELECTION
# ======================================================
def test_the_most_relevant_role_is_chosen(resume):
    letter = cover_letter.compose(resume, job())
    assert "Backend Developer" in letter.to_text()
    assert "Support Engineer" not in letter.to_text()


def test_the_highlighted_bullet_is_relevant(resume):
    letter = cover_letter.compose(resume, job(
        description="We need Playwright automation."))
    assert "Playwright" in letter.to_text()


def test_skills_named_in_the_title_lead(resume):
    letter = cover_letter.compose(resume, job(
        title="Playwright Automation Engineer",
        description="Also Django and PostgreSQL."))
    assert letter.skills_used[0] == "Playwright"


def test_only_three_skills_are_named(resume):
    letter = cover_letter.compose(resume, job())
    assert len(letter.skills_used) <= 3


def test_nothing_is_invented(resume):
    """Every skill named must actually be in the resume."""
    letter = cover_letter.compose(resume, job(
        description="Django, PostgreSQL, Kubernetes, Rust and COBOL."))
    resume_text = resume.full_text().lower()
    for skill in letter.skills_used:
        assert skill.lower() in resume_text


# ======================================================
# FALLBACKS
# ======================================================
def test_a_missing_company_reads_naturally(resume):
    """OnlineJobs.ph publishes no employer, which is the common case."""
    letter = cover_letter.compose(resume, job(company=""))
    assert "role at your company" in letter.to_text()
    assert "at ," not in letter.to_text()


def test_an_address_is_never_used_as_a_headline():
    """
    Regression: an imported address landed in the headline, producing
    "I work as a Quezon City, Metro Manila, Philippines".
    """
    odd = resume_model.parse_markdown(
        "# Jane Dev\nQuezon City, Metro Manila, Philippines\n\n"
        "## Experience\n\n### Dev — Acme\n2021 - Present\n- Built things.\n")
    letter = cover_letter.compose(odd, job())
    assert "Quezon City" not in " ".join(letter.paragraphs)
    assert "I work as a developer" in letter.to_text()


def test_a_resume_with_no_experience_section_still_produces_a_letter():
    sparse = resume_model.parse_markdown(
        "# Jane Dev\nDeveloper\njane@example.com\n\n## Skills\n\nPython\n")
    letter = cover_letter.compose(sparse, job())
    assert letter.to_text().strip()
    assert "my current role" in letter.to_text()


def test_an_unmatched_job_still_produces_a_letter(resume):
    letter = cover_letter.compose(resume, job(
        title="Pastry Chef", description="Baking bread."))
    assert letter.skills_used == []
    assert "the tools you list" in letter.to_text()


# ======================================================
# STRUCTURE
# ======================================================
def test_letter_has_the_expected_parts(resume):
    letter = cover_letter.compose(resume, job())
    text = letter.to_text()
    assert "Dear Hiring Manager," in text
    assert "Sincerely," in text
    assert "Jane Dev" in text
    assert "jane@example.com" in text


def test_a_named_recipient_is_used(resume):
    letter = cover_letter.compose(resume, job(), recipient="Ms Santos")
    assert "Dear Ms Santos," in letter.to_text()


def test_no_placeholder_survives_into_the_letter(resume):
    """An unfilled $placeholder in the output would be embarrassing."""
    for tone in cover_letter.available_tones():
        text = cover_letter.compose(resume, job(), tone=tone).to_text()
        assert "$" not in text, f"{tone} left a placeholder unfilled"


def test_no_empty_paragraphs(resume):
    letter = cover_letter.compose(resume, job())
    assert all(paragraph.strip() for paragraph in letter.paragraphs)


# ======================================================
# TONES
# ======================================================
def test_all_shipped_tones_exist():
    assert set(cover_letter.available_tones()) >= {"direct", "warm",
                                                   "technical"}


def test_tones_produce_different_letters(resume):
    texts = {tone: cover_letter.compose(resume, job(), tone=tone).to_text()
             for tone in ("direct", "warm", "technical")}
    assert len(set(texts.values())) == 3


def test_an_unknown_tone_is_rejected(resume):
    with pytest.raises(FileNotFoundError, match="No template"):
        cover_letter.compose(resume, job(), tone="shouty")


def test_template_comments_never_appear(resume):
    for tone in cover_letter.available_tones():
        text = cover_letter.compose(resume, job(), tone=tone).to_text()
        assert "Available:" not in text
        assert not any(line.startswith("#") for line in text.splitlines())

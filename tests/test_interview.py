"""
Tests for Standard-mode interview preparation.

interview.prepare() is a pure transform of (resume, job, ScoreExplanation), so
these build the explanation by hand — no database. The guarantee it must keep:
every talking point is a bullet already in the resume, never invented, and a
matched skill is paired with the bullet that evidences it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interview
import resume_model
from explain import ScoreExplanation

RESUME_MD = """# Jane Dev
Python Developer
jane@example.com

## Skills

Python, PostgreSQL, Playwright

## Experience

### Backend Developer — Acme Corp
2021 - Present
- Built internal tooling used by the operations team.
- Wrote Python scripts that cut manual review time by 30%.
"""


def resume():
    return resume_model.parse_markdown(RESUME_MD)


def job(**overrides) -> dict:
    base = {"title": "Python Developer", "company": "Globe"}
    return {**base, **overrides}


def explanation(**overrides) -> ScoreExplanation:
    base = dict(job_key="jobstreet:id:1", score_percent=70.0,
                title_matches=["Python"], body_matches=["PostgreSQL"],
                missing=["Kubernetes", "Terraform"],
                demand={"Kubernetes": 9, "Terraform": 3})
    return ScoreExplanation(**{**base, **overrides})


# ======================================================
# STRENGTHS ARE GROUNDED IN REAL BULLETS
# ======================================================
def test_a_matched_skill_is_paired_with_the_bullet_that_evidences_it():
    prep = interview.prepare(resume(), job(), explanation())
    python = next(p for p in prep.strengths if p.skill == "Python")
    assert "Python scripts" in python.bullet


def test_the_quantified_bullet_wins_when_several_mention_a_skill():
    """A bullet citing a number is the stronger talking point."""
    prep = interview.prepare(resume(), job(), explanation())
    python = next(p for p in prep.strengths if p.skill == "Python")
    assert "30%" in python.bullet


def test_a_matched_skill_with_no_bullet_still_appears_without_one():
    prep = interview.prepare(
        resume(), job(),
        explanation(title_matches=["PostgreSQL"], body_matches=[]))
    postgres = next(p for p in prep.strengths if p.skill == "PostgreSQL")
    assert postgres.bullet == ""  # listed as a skill, not in any bullet


# ======================================================
# QUESTIONS
# ======================================================
def test_every_matched_skill_yields_an_experience_question():
    prep = interview.prepare(resume(), job(), explanation())
    experience = [q for q in prep.questions if q.category == "Experience"]
    assert any("Python" in q.prompt for q in experience)
    assert any("PostgreSQL" in q.prompt for q in experience)


def test_gaps_are_ranked_by_demand_and_yield_gap_questions():
    prep = interview.prepare(resume(), job(), explanation())
    # Kubernetes (demand 9) must rank ahead of Terraform (demand 3)
    assert prep.gaps[0] == "Kubernetes"
    gap_prompts = [q.prompt for q in prep.questions if q.category == "Gap"]
    assert any("Kubernetes" in prompt for prompt in gap_prompts)


def test_behavioural_questions_name_the_role_and_company():
    prep = interview.prepare(resume(), job(), explanation())
    behavioural = [q for q in prep.questions if q.category == "Behavioural"]
    assert any("Python Developer" in q.prompt and "Globe" in q.prompt
               for q in behavioural)


def test_a_measurable_impact_question_cites_a_real_bullet():
    prep = interview.prepare(resume(), job(), explanation())
    impact = next((q for q in prep.questions if "measurable impact" in q.prompt),
                  None)
    assert impact is not None
    assert "30%" in impact.hint


def test_a_bare_year_is_not_treated_as_measurable_impact():
    """A graduation/certification year must not be cited as an achievement."""
    year_only = resume_model.parse_markdown(
        "# Jane Dev\nDeveloper\njane@example.com\n\n## Skills\n\nPython\n\n"
        "## Experience\n\n### Developer — Acme\n2021 - Present\n"
        "- Completed the Web Development Bootcamp in 2023.\n"
        "- Built internal tooling for the team.\n")
    prep = interview.prepare(year_only, job(), explanation())
    impact = [q for q in prep.questions if "measurable impact" in q.prompt]
    assert impact == []  # nothing cites a real metric, so the question is dropped


# ======================================================
# NOTHING IS INVENTED
# ======================================================
def test_no_talking_point_is_absent_from_the_resume():
    prep = interview.prepare(resume(), job(), explanation())
    resume_text = resume().full_text()
    for point in prep.strengths:
        if point.bullet:
            assert point.bullet in resume_text


def test_a_job_matching_nothing_still_produces_a_usable_sheet():
    prep = interview.prepare(
        resume(), job(title="Pastry Chef", company="Sweet Co"),
        explanation(title_matches=[], body_matches=[], missing=[], demand={}))
    assert prep.strengths == []
    # Still offers behavioural questions and a readable sheet
    assert any(q.category == "Behavioural" for q in prep.questions)
    assert prep.lines


def test_the_sheet_renders_to_lines():
    prep = interview.prepare(resume(), job(), explanation())
    assert prep.lines
    assert any("Likely questions" in line for line in prep.lines)

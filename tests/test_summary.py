"""
Tests for the Standard-mode job summary.

Two things must hold: section extraction pulls the right blocks out of a
structured advert and leaves fields empty when a heading is absent (no
guessing), and the red-flag rules catch the obvious scams without crying wolf
on ordinary postings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import summary

STRUCTURED = """We are hiring a Python Developer.

Responsibilities:
- Build and maintain internal automation tools
- Write tests and review pull requests

Requirements:
- 3+ years of Python
- Experience with PostgreSQL and REST APIs

Nice to have:
- Docker and AWS exposure

Benefits:
- HMO on day one
- Work-from-home setup
"""


def job(**overrides) -> dict:
    base = {"job_key": "jobstreet:id:1", "title": "Python Developer",
            "company": "Acme", "description": STRUCTURED, "salary": "",
            "work_arrangement": "Remote", "required_years": 3}
    return {**base, **overrides}


# ======================================================
# SECTION EXTRACTION
# ======================================================
def test_the_four_sections_are_pulled_out():
    result = summary.summarise(job())
    assert any("automation tools" in item for item in result.responsibilities)
    assert any("3+ years of Python" in item for item in result.requirements)
    assert any("Docker" in item for item in result.nice_to_have)
    assert any("HMO" in item for item in result.benefits)


def test_bullet_markers_are_stripped():
    result = summary.summarise(job())
    assert all(not item.startswith(("-", "•", "*"))
               for item in result.responsibilities)


def test_a_freeform_advert_leaves_sections_empty():
    """No headings — nothing is invented; has_sections() is False."""
    freeform = job(description="Looking for a rockstar dev to join our team. "
                               "Message me if interested!")
    result = summary.summarise(freeform)
    assert result.has_sections() is False
    assert result.responsibilities == []
    assert any("no clear sections" in line for line in result.lines)


def test_a_heading_is_not_matched_inside_a_sentence():
    """A long sentence mentioning 'requirements' is not treated as a heading."""
    prose = job(description="This role has demanding requirements that evolve "
                            "with the product over many quarters of work.")
    result = summary.summarise(prose)
    assert result.requirements == []


def test_stored_facts_are_carried_through():
    result = summary.summarise(job(salary="PHP 60,000 - 80,000"))
    assert result.work_arrangement == "Remote"
    assert result.salary_text == "PHP 60,000 - 80,000"
    assert result.required_years == 3


def test_work_arrangement_defaults_to_unstated():
    result = summary.summarise(job(work_arrangement=""))
    assert result.work_arrangement == "Unstated"


# ======================================================
# RED FLAGS
# ======================================================
def test_a_clean_advert_has_no_red_flags():
    assert summary.summarise(job()).red_flags == []


def test_a_payment_request_is_flagged():
    result = summary.summarise(job(
        description="Pay a registration fee of 500 to start immediately."))
    assert any("fee" in flag.lower() for flag in result.red_flags)


def test_unlimited_earning_language_is_flagged():
    result = summary.summarise(job(
        description="Be your own boss! Unlimited earning potential from home."))
    assert any("unlimited earning" in flag.lower()
               for flag in result.red_flags)


def test_crypto_recruitment_is_flagged():
    result = summary.summarise(job(
        description="Join our forex trading platform as an agent and recruit "
                    "investors for referral bonuses."))
    assert any("crypto/forex" in flag.lower() for flag in result.red_flags)


def test_no_company_plus_personal_email_is_flagged():
    result = summary.summarise(job(
        company="", description="Send your CV to hiring.fast@gmail.com today."))
    assert any("personal email" in flag.lower() for flag in result.red_flags)


def test_a_senior_role_with_only_competitive_pay_is_flagged():
    result = summary.summarise(job(
        required_years=6,
        description="Senior engineer. Competitive salary and great team."))
    assert any("competitive" in flag.lower() for flag in result.red_flags)


def test_a_junior_competitive_role_is_not_flagged():
    """The competitive-pay flag is for senior roles only."""
    result = summary.summarise(job(
        required_years=1,
        description="Junior role. Competitive salary for fresh graduates."))
    assert not any("competitive" in flag.lower() for flag in result.red_flags)

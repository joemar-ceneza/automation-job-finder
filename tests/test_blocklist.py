"""Tests for the company and title blocklists."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import scraper_common
from scraper_common import JobListing


def listing(title: str, company: str = "") -> JobListing:
    return JobListing(job_key=f"jobstreet:id:{title}", title=title,
                      company=company, location="", teaser="", url="",
                      source="jobstreet")


@pytest.fixture(autouse=True)
def clean_blocklists(monkeypatch):
    monkeypatch.setattr(config, "BLOCKLISTED_COMPANIES", [])
    monkeypatch.setattr(config, "BLOCKLISTED_TITLE_KEYWORDS", [])


def titles(kept) -> list[str]:
    return [item.title for item in kept]


# ======================================================
# NO BLOCKLIST CONFIGURED
# ======================================================
def test_empty_blocklists_keep_everything():
    items = [listing("Python Developer", "Acme"), listing("Sales Lead", "Beta")]
    assert scraper_common.filter_blocklisted(items) == items


# ======================================================
# COMPANY
# ======================================================
def test_company_blocklist_is_a_substring_match(monkeypatch):
    monkeypatch.setattr(config, "BLOCKLISTED_COMPANIES", ["acme"])
    kept = scraper_common.filter_blocklisted([
        listing("Dev", "ACME Recruitment Inc"),
        listing("Dev", "Globe Telecom"),
    ])
    assert [item.company for item in kept] == ["Globe Telecom"]


def test_blank_company_survives_a_company_blocklist(monkeypatch):
    """OnlineJobs.ph hides employers — do not drop everything it returns."""
    monkeypatch.setattr(config, "BLOCKLISTED_COMPANIES", ["acme"])
    kept = scraper_common.filter_blocklisted([listing("Dev", "")])
    assert len(kept) == 1


# ======================================================
# TITLE
# ======================================================
def test_title_keyword_blocks_the_role(monkeypatch):
    monkeypatch.setattr(config, "BLOCKLISTED_TITLE_KEYWORDS", ["senior"])
    kept = scraper_common.filter_blocklisted([
        listing("Senior Python Developer"),
        listing("Python Developer"),
    ])
    assert titles(kept) == ["Python Developer"]


def test_title_keyword_matches_whole_words_only(monkeypatch):
    """'lead' must not block 'Leadership', 'manager' not 'Management'."""
    monkeypatch.setattr(config, "BLOCKLISTED_TITLE_KEYWORDS",
                        ["lead", "manager"])
    kept = scraper_common.filter_blocklisted([
        listing("Lead Developer"),          # blocked
        listing("Leadership Trainee"),      # kept
        listing("Engineering Manager"),     # blocked
        listing("Management Trainee"),      # kept
    ])
    assert titles(kept) == ["Leadership Trainee", "Management Trainee"]


def test_title_blocklist_applies_without_a_company(monkeypatch):
    """This is why title filtering matters more than company filtering."""
    monkeypatch.setattr(config, "BLOCKLISTED_TITLE_KEYWORDS", ["sales"])
    kept = scraper_common.filter_blocklisted([
        listing("Sales Executive", ""),
        listing("Python Developer", ""),
    ])
    assert titles(kept) == ["Python Developer"]


def test_dotted_keywords_are_escaped(monkeypatch):
    """'.net' is a regex hazard if not escaped."""
    monkeypatch.setattr(config, "BLOCKLISTED_TITLE_KEYWORDS", [".net"])
    kept = scraper_common.filter_blocklisted([
        listing(".NET Developer"),
        listing("Python Developer"),
    ])
    assert titles(kept) == ["Python Developer"]


def test_both_blocklists_apply_together(monkeypatch):
    monkeypatch.setattr(config, "BLOCKLISTED_COMPANIES", ["badcorp"])
    monkeypatch.setattr(config, "BLOCKLISTED_TITLE_KEYWORDS", ["intern"])
    kept = scraper_common.filter_blocklisted([
        listing("Python Developer", "BadCorp"),
        listing("Intern Developer", "Good Co"),
        listing("Python Developer", "Good Co"),
    ])
    assert len(kept) == 1
    assert kept[0].company == "Good Co"
    assert kept[0].title == "Python Developer"

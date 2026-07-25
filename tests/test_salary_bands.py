"""
Tests for Standard-mode salary banding.

The comparison must stay honest: suppressed below the sample threshold, banded
by percentile above it, and always labelled with the sample it came from. The
job's own yearly/hourly figures are shown either way, since deriving them needs
no corpus.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import salary_bands


def job(low=None, high=None, role="python developer") -> dict:
    return {"job_key": "jobstreet:id:1", "search_keyword": role,
            "salary_min": low, "salary_max": high}


def corpus(mids: list[int], role="python developer") -> list[dict]:
    """A salaried corpus where each job's midpoint is the given value."""
    return [job(low=mid, high=mid, role=role) for mid in mids]


# ======================================================
# DERIVED FIGURES (no corpus needed)
# ======================================================
def test_yearly_and_hourly_are_derived():
    result = salary_bands.assess(job(50000, 70000), [])
    assert result.has_salary is True
    assert result.monthly == 60000
    assert result.yearly == 720000          # ×12
    assert result.yearly_13th == 780000     # ×13 (PH 13th month)
    assert result.hourly == round(60000 / 176)


def test_a_single_bound_still_yields_a_figure():
    result = salary_bands.assess(job(low=40000), [])
    assert result.monthly == 40000


def test_no_salary_is_reported_as_such():
    result = salary_bands.assess(job(), [])
    assert result.has_salary is False
    assert any("no salary" in line.lower() for line in result.lines)


# ======================================================
# SUPPRESSION BELOW THE SAMPLE THRESHOLD
# ======================================================
def test_a_thin_corpus_is_not_banded_but_figures_remain():
    result = salary_bands.assess(job(50000, 50000), corpus([50000] * 5))
    assert result.enough_sample is False
    assert result.band == ""
    assert result.monthly == 50000          # own figures still shown
    assert any("too few" in line.lower() for line in result.lines)


# ======================================================
# BANDING ABOVE THE THRESHOLD
# ======================================================
def _wide_corpus():
    # 40 postings evenly spread 30k–90k → p25≈45k, median≈60k, p75≈75k.
    return corpus(list(range(30000, 90001, 1500)))


def test_a_high_salary_bands_above():
    result = salary_bands.assess(job(95000, 95000), _wide_corpus())
    assert result.enough_sample is True
    assert result.sample_size >= config.SALARY_MIN_SAMPLES
    assert result.band == "Above"


def test_a_midrange_salary_bands_competitive():
    result = salary_bands.assess(job(60000, 60000), _wide_corpus())
    assert result.band == "Competitive"


def test_a_low_salary_bands_below():
    result = salary_bands.assess(job(32000, 32000), _wide_corpus())
    assert result.band == "Below"


def test_corpus_stats_are_populated():
    result = salary_bands.assess(job(60000, 60000), _wide_corpus())
    assert result.corpus_median is not None
    assert result.corpus_min is not None and result.corpus_max is not None
    assert result.p25 < result.corpus_median < result.p75


def test_a_job_without_pay_against_a_full_corpus_shows_no_band():
    result = salary_bands.assess(job(), _wide_corpus())
    assert result.has_salary is False
    assert result.band == ""
    assert result.enough_sample is True     # corpus is fine; the job has no pay

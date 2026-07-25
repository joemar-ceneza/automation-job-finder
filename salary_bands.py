"""
salary_bands.py
Standard-mode salary analytics: band a job's pay against the roles like it that
you've actually scraped, and derive the yearly and hourly figures the advert
leaves implicit.

Two honesty rules the design turns on. First, the benchmark is *your corpus*,
not a national survey — the comparison is always labelled with the sample it
came from, and suppressed entirely below SALARY_MIN_SAMPLES because a percentile
drawn from a handful of postings is a guess wearing a statistic's clothes. Most
PH ads state no salary at all, so that sample is always smaller than the job
count. Second, the job's own yearly/hourly figures are shown even when the
corpus is too thin to band against — deriving ×12 (and a 13th-month note) and
÷176 needs no comparison, only the number the advert already gave.

Pure over its inputs: the caller passes the job and the corpus of salaried jobs;
this computes, it does not query.
"""
import statistics
from dataclasses import dataclass, field

import config

# Standard PH working hours per month (≈ 22 days × 8 hours) for the hourly rate.
_HOURS_PER_MONTH = 176


@dataclass
class SalaryAssessment:
    """How one job's pay reads against the roles like it that you track."""
    role: str = ""
    has_salary: bool = False           # this advert states a monthly salary
    monthly: int | None = None         # its monthly midpoint
    yearly: int | None = None          # × 12
    yearly_13th: int | None = None     # × 13 (PH 13th-month pay)
    hourly: int | None = None          # ÷ 176

    enough_sample: bool = False        # corpus large enough to band against
    sample_size: int = 0
    band: str = ""                     # Below | Competitive | Above
    corpus_min: int | None = None
    corpus_median: int | None = None
    corpus_max: int | None = None
    p25: int | None = None
    p75: int | None = None
    lines: list[str] = field(default_factory=list)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _number(value) -> float | None:
    """Coerce a stored salary field to a float, or None when absent/blank."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _monthly_mid(job: dict) -> float | None:
    """A job's monthly salary midpoint from its min/max, or None."""
    low = _number(job.get("salary_min"))
    high = _number(job.get("salary_max"))
    values = [value for value in (low, high) if value is not None]
    return sum(values) / len(values) if values else None


def _peso(amount: float | None) -> str:
    return f"₱{round(amount):,}" if amount is not None else "—"


def _describe(assessment: SalaryAssessment) -> list[str]:
    if not assessment.has_salary:
        return ["This advert states no salary."]

    lines = [f"Stated pay ≈ {_peso(assessment.monthly)}/month "
             f"({_peso(assessment.yearly)}/yr, "
             f"{_peso(assessment.yearly_13th)} with 13th month, "
             f"≈ {_peso(assessment.hourly)}/hour)."]
    if assessment.enough_sample:
        lines.append(
            f"That is **{assessment.band}** versus {assessment.sample_size} "
            f"'{assessment.role}' postings you've tracked "
            f"(median {_peso(assessment.corpus_median)}, "
            f"middle half {_peso(assessment.p25)}–{_peso(assessment.p75)}).")
    else:
        lines.append(
            f"Only {assessment.sample_size} '{assessment.role}' posting(s) with "
            f"a salary tracked — too few to say competitive or not "
            f"(need {config.SALARY_MIN_SAMPLES}).")
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def assess(job: dict, corpus: list[dict]) -> SalaryAssessment:
    """
    Bands one job's pay against a corpus of salaried jobs and derives its
    yearly/hourly figures. The corpus should already be the same-role salaried
    postings; this filters to those that actually parse a number.
    """
    assessment = SalaryAssessment(role=job.get("search_keyword") or "similar")
    monthly = _monthly_mid(job)

    if monthly is not None:
        assessment.has_salary = True
        assessment.monthly = round(monthly)
        assessment.yearly = round(monthly * 12)
        assessment.yearly_13th = round(monthly * 13)
        assessment.hourly = round(monthly / _HOURS_PER_MONTH)

    corpus_mids = [mid for other in corpus
                   if (mid := _monthly_mid(other)) is not None]
    assessment.sample_size = len(corpus_mids)

    if len(corpus_mids) >= config.SALARY_MIN_SAMPLES:
        assessment.enough_sample = True
        quartiles = statistics.quantiles(corpus_mids, n=4)  # [p25, p50, p75]
        assessment.p25 = round(quartiles[0])
        assessment.corpus_median = round(statistics.median(corpus_mids))
        assessment.p75 = round(quartiles[2])
        assessment.corpus_min = round(min(corpus_mids))
        assessment.corpus_max = round(max(corpus_mids))
        if monthly is not None:
            if monthly < quartiles[0]:
                assessment.band = "Below"
            elif monthly > quartiles[2]:
                assessment.band = "Above"
            else:
                assessment.band = "Competitive"

    assessment.lines = _describe(assessment)
    return assessment

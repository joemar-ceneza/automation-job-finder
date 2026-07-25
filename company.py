"""
company.py
Standard-mode company intelligence — the honest subset.

What this deliberately does NOT do: company size, employee ratings, and
interview-difficulty scores. Those live on Glassdoor and LinkedIn, whose terms
prohibit automated collection and whose bot protection is heavier than anything
this project already works around. Shipping empty columns for them would be
worse than not having them.

What survives is better sourced anyway: what your own database knows about an
employer. Posting frequency, how long they've been advertising, what they pay
on average, which roles they repeat, and where. A company that posts the same
role every month is telling you something a star rating never would — and every
figure here is arithmetic over rows you scraped yourself.

Pure over its inputs: the caller passes the company's postings; this computes.
"""
import datetime
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class CompanyProfile:
    """What your corpus knows about one employer."""
    name: str = ""
    postings: int = 0
    first_seen: str = ""
    last_seen: str = ""
    days_active: int | None = None
    posts_per_month: float | None = None
    repeated_roles: list[tuple[str, int]] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    arrangements: list[str] = field(default_factory=list)
    salaried_postings: int = 0
    average_salary: int | None = None
    salary_low: int | None = None
    salary_high: int | None = None
    average_score: float | None = None
    lines: list[str] = field(default_factory=list)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _date(value: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _midpoint(job: dict) -> float | None:
    values = [value for value in (_number(job.get("salary_min")),
                                  _number(job.get("salary_max")))
              if value is not None]
    return sum(values) / len(values) if values else None


def _common(values: list[str], limit: int = 4) -> list[str]:
    counted = Counter(value.strip() for value in values if value and value.strip())
    return [value for value, _count in counted.most_common(limit)]


def _describe(profile: CompanyProfile) -> list[str]:
    if not profile.postings:
        return ["Nothing tracked for this company yet."]

    lines = [f"{profile.postings} posting(s) tracked, first seen "
             f"{profile.first_seen[:10]}."]
    if profile.posts_per_month and profile.days_active and \
            profile.days_active >= 30:
        lines.append(f"Advertising for {profile.days_active} days — about "
                     f"{profile.posts_per_month:.1f} posting(s) a month.")

    repeated = [f"{role} ×{count}" for role, count in profile.repeated_roles
                if count > 1]
    if repeated:
        lines.append("Repeatedly hiring for: " + ", ".join(repeated)
                     + " — a role posted again and again is either growing or "
                       "churning; worth asking which.")
    if profile.average_salary:
        lines.append(f"Advertises ₱{profile.average_salary:,}/month on average "
                     f"across {profile.salaried_postings} posting(s) that state "
                     f"pay (₱{profile.salary_low:,}–₱{profile.salary_high:,}).")
    elif profile.postings:
        lines.append("None of their postings state a salary.")
    if profile.locations:
        lines.append("Locations: " + ", ".join(profile.locations))
    if profile.arrangements:
        lines.append("Arrangement: " + ", ".join(profile.arrangements))
    if profile.average_score is not None:
        lines.append(f"Their postings match your resume {profile.average_score}% "
                     "on average.")
    lines.append("Company size, ratings, and interview difficulty are "
                 "deliberately absent — that data cannot be collected within "
                 "the terms of the sites that hold it.")
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def profile(company: str, postings: list[dict]) -> CompanyProfile:
    """
    Builds a profile of one employer from their postings in your corpus.
    Deterministic; every figure is a count or an average over rows you scraped.
    """
    result = CompanyProfile(name=company, postings=len(postings))
    if not postings:
        result.lines = _describe(result)
        return result

    dates = sorted(date for date in
                   (_date(job.get("first_seen", "")) for job in postings)
                   if date)
    if dates:
        result.first_seen = dates[0].isoformat()
        result.last_seen = dates[-1].isoformat()
        span = (dates[-1] - dates[0]).days
        result.days_active = span
        # Below a month the rate is an artefact of when you started scraping,
        # not of how they hire — leave it out rather than extrapolate.
        if span >= 30:
            result.posts_per_month = round(len(postings) / (span / 30.0), 1)

    titles = Counter((job.get("title") or "").strip()
                     for job in postings if (job.get("title") or "").strip())
    result.repeated_roles = titles.most_common(5)
    result.locations = _common([job.get("location", "") for job in postings])
    result.arrangements = _common(
        [job.get("work_arrangement", "") for job in postings])

    midpoints = [mid for job in postings if (mid := _midpoint(job)) is not None]
    result.salaried_postings = len(midpoints)
    if midpoints:
        result.average_salary = round(sum(midpoints) / len(midpoints))
        result.salary_low = round(min(midpoints))
        result.salary_high = round(max(midpoints))

    scores = [score for job in postings
              if (score := _number(job.get("score_percent"))) is not None]
    if scores:
        result.average_score = round(sum(scores) / len(scores), 1)

    result.lines = _describe(result)
    return result

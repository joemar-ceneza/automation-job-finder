"""
portfolio.py
Standard-mode portfolio matching: which of your projects to show for a given
job, and why.

The matching deliberately has no algorithm of its own. A project's technology
tags stand in for a resume's skills and go through matcher.score_against(), the
same scorer that ranked the job for you in the first place — so a project tops
the list for a job for exactly the reason that job ranked, and there is no
second scoring implementation to drift out of step with the first.

The portfolio lives in a TOML file you maintain (config.PORTFOLIO_FILE). Nothing
writes to it. A missing or malformed file is reported and treated as an empty
portfolio rather than raised, because a broken side-feature should never stop
you looking at a job.
"""
import logging
import os
import tomllib
from dataclasses import dataclass, field

import config
import matcher


@dataclass
class Project:
    """One project you'd point an employer at."""
    name: str
    url: str = ""
    summary: str = ""
    tech: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)


@dataclass
class ProjectMatch:
    """How well one project fits one job."""
    project: Project
    score_percent: float = 0.0
    matched: list[str] = field(default_factory=list)      # tags the job asks for
    title_matches: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)    # tags it doesn't


@dataclass
class PortfolioMatch:
    """The ranked portfolio for one job."""
    matches: list[ProjectMatch] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def best(self) -> ProjectMatch | None:
        return self.matches[0] if self.matches else None


# ======================================================
# LOADING
# ======================================================
def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def load(path: str | None = None) -> list[Project]:
    """
    Reads the portfolio file. Returns [] when it is absent or unreadable —
    never raises, and says clearly what went wrong so it can be fixed.
    """
    path = path or config.PORTFOLIO_FILE
    if not os.path.exists(path):
        logging.info("No portfolio file at %s — create one to match your "
                     "projects against jobs.", path)
        return []
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError) as error:
        logging.error("Could not read the portfolio at %s: %s", path, error)
        return []

    projects = []
    for entry in data.get("project", []):
        name = str(entry.get("name", "")).strip()
        if not name:
            logging.warning("Skipping a portfolio entry with no name.")
            continue
        projects.append(Project(
            name=name,
            url=str(entry.get("url", "")).strip(),
            summary=" ".join(str(entry.get("summary", "")).split()),
            tech=_as_list(entry.get("tech")),
            highlights=_as_list(entry.get("highlights")),
        ))
    return projects


# ======================================================
# MATCHING
# ======================================================
def _describe(result: PortfolioMatch) -> list[str]:
    if not result.matches:
        return ["No projects to match. Add some to data/portfolio.toml."]
    lines = []
    for index, match in enumerate(result.matches, 1):
        lines.append(f"{index}. {match.project.name} — {match.score_percent}%")
        if match.matched:
            lines.append(f"     demonstrates: {', '.join(match.matched)}")
        if match.project.url:
            lines.append(f"     {match.project.url}")
    best = result.best
    if best and best.score_percent > 0:
        lines.append("")
        lines.append(f"Lead with {best.project.name} — it shows "
                     f"{', '.join(best.matched[:3])}, which this advert asks "
                     "for.")
    elif best:
        lines.append("")
        lines.append("None of your projects use what this advert asks for. "
                     "Lead with the closest one and be explicit about the "
                     "transferable parts.")
    return lines


def match_job(job: dict, projects: list[Project] | None = None) -> PortfolioMatch:
    """
    Ranks your projects against one job, best first. Uses the job scorer with
    each project's tech tags in place of resume skills, so the ranking is the
    same arithmetic that scored the job itself.
    """
    projects = load() if projects is None else projects
    result = PortfolioMatch()
    if not projects:
        result.lines = _describe(result)
        return result

    title = job.get("title", "") or ""
    body = " ".join(part for part in (job.get("teaser", ""),
                                      job.get("description", "")) if part)

    for project in projects:
        score, title_matches, body_matches = matcher.score_against(
            title, body, project.tech)
        matched = title_matches + body_matches
        result.matches.append(ProjectMatch(
            project=project,
            score_percent=score,
            matched=matched,
            title_matches=title_matches,
            unmatched=[tag for tag in project.tech if tag not in matched],
        ))

    # Best first; ties keep portfolio order, which is the order you chose.
    result.matches.sort(key=lambda match: match.score_percent, reverse=True)
    result.lines = _describe(result)
    return result

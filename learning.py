"""
learning.py
Standard-mode learning recommendations: which missing skill to learn first, in
an order that respects what has to come before what, with an hours estimate for
the whole plan.

Deterministic and built from data you already have. Demand comes from your own
corpus ("Docker appears in 41 of the jobs you track"), the difficulty, hours and
prerequisites come from the curated learning_map, and the order comes from a
topological sort over those prerequisites — so Docker lands before Kubernetes
whether or not you asked for Docker. A prerequisite you already have is
satisfied and dropped; one you lack is pulled into the plan, because there is no
point recommending Kubernetes to someone who has never run a container.

"Do I already have this?" is judged alias-aware against your resume *text*, the
same way explain.py judges it — the extractor speaks MASTER_SKILLS ("React JS")
while a resume says "React.js", and comparing names would send you off to learn
what you already know.
"""
from dataclasses import dataclass, field

import learning_map
from learning_map import DIFFICULTY_RANK
from resume_parser import skill_in_text

# Guard against a malformed map: never walk deeper than this chasing
# prerequisites, so a bad edit is a shallow plan rather than a hang.
_MAX_DEPTH = 6


@dataclass
class LearningStep:
    """One skill to learn, and what it takes."""
    skill: str
    demand: int = 0                 # jobs in your corpus asking for it
    difficulty: str = ""
    hours: int | None = None
    resources: list[tuple[str, str]] = field(default_factory=list)
    is_prerequisite: bool = False   # pulled in to support a later step
    unlocks: list[str] = field(default_factory=list)   # what it leads to
    mapped: bool = True             # False when the map has no entry yet


@dataclass
class LearningPlan:
    """An ordered study plan for the gaps that matter most."""
    steps: list[LearningStep] = field(default_factory=list)
    total_hours: int = 0
    unmapped: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _already_have(skill: str, resume_text_lower: str) -> bool:
    return skill_in_text(skill, resume_text_lower)


def _missing_prerequisites(skill: str, resume_text_lower: str,
                           depth: int = 0) -> list[str]:
    """
    Every prerequisite of a skill that the resume does not evidence, deepest
    first. A prerequisite you already have stops the walk down that branch —
    its own prerequisites are moot.
    """
    if depth >= _MAX_DEPTH:
        return []
    entry = learning_map.entry_for(skill)
    if entry is None:
        return []

    needed: list[str] = []
    for prerequisite in entry.prerequisites:
        if _already_have(prerequisite, resume_text_lower):
            continue
        needed.extend(_missing_prerequisites(prerequisite, resume_text_lower,
                                             depth + 1))
        needed.append(prerequisite)
    return needed


def _ordered(skills: list[str], resume_text_lower: str) -> list[str]:
    """
    Topological order over the map's prerequisites: a skill never appears
    before something it depends on. Ties keep the demand order they arrived in.
    A cycle from a bad map edit is broken rather than followed.
    """
    ordered: list[str] = []
    placed: set[str] = set()
    visiting: set[str] = set()

    def visit(skill: str, depth: int = 0) -> None:
        if skill in placed or skill in visiting or depth >= _MAX_DEPTH:
            return                       # already placed, or a cycle — stop
        visiting.add(skill)
        entry = learning_map.entry_for(skill)
        for prerequisite in (entry.prerequisites if entry else ()):
            if (prerequisite in skills
                    and not _already_have(prerequisite, resume_text_lower)):
                visit(prerequisite, depth + 1)
        visiting.discard(skill)
        placed.add(skill)
        ordered.append(skill)

    for skill in skills:
        visit(skill)
    return ordered


def _describe(plan: LearningPlan) -> list[str]:
    if not plan.steps:
        return ["Nothing to recommend — your resume already covers the skills "
                "your tracked jobs ask for most."]

    lines = []
    for index, step in enumerate(plan.steps, 1):
        detail = []
        if step.demand:
            detail.append(f"{step.demand} job(s)")
        if step.difficulty:
            detail.append(step.difficulty)
        if step.hours:
            detail.append(f"~{step.hours}h")
        suffix = f" [{' · '.join(detail)}]" if detail else ""
        marker = " (foundation for " + ", ".join(step.unlocks) + ")" \
            if step.is_prerequisite and step.unlocks else ""
        lines.append(f"{index}. {step.skill}{suffix}{marker}")
        for label, url in step.resources:
            lines.append(f"     {label}: {url}")

    if plan.total_hours:
        lines.append("")
        lines.append(f"Roughly {plan.total_hours} hours of study to close all "
                     f"{len(plan.steps)}.")
    if plan.unmapped:
        lines.append(f"No study estimate on file for: "
                     f"{', '.join(plan.unmapped)}.")
    return lines


# ======================================================
# PUBLIC API
# ======================================================
def plan(resume_text: str, demand_rows: list[dict],
         limit: int = 5) -> LearningPlan:
    """
    Builds an ordered study plan from your corpus's skill demand.

    demand_rows are {"skill", "demand"} dicts (db_handler.skill_demand's shape).
    Skills the resume already evidences are dropped; the `limit` most-demanded
    of the rest become targets, and any prerequisite you lack is pulled in
    ahead of the skill that needs it. Pure over its inputs.
    """
    lowered = (resume_text or "").lower()

    missing = [(row["skill"], int(row.get("demand") or 0))
               for row in demand_rows
               if row.get("skill") and not _already_have(row["skill"], lowered)]
    demand_by_skill = dict(missing)

    def rank(pair: tuple[str, int]) -> tuple[int, int, str]:
        skill, demand = pair
        entry = learning_map.entry_for(skill)
        difficulty = DIFFICULTY_RANK.get(entry.difficulty if entry else "", 1)
        return (-demand, difficulty, skill)

    targets = [skill for skill, _demand in sorted(missing, key=rank)[:limit]]

    # Pull in the foundations those targets need but the resume lacks.
    unlocks: dict[str, list[str]] = {}
    wanted: list[str] = []
    for target in targets:
        for prerequisite in _missing_prerequisites(target, lowered):
            unlocks.setdefault(prerequisite, [])
            if target not in unlocks[prerequisite]:
                unlocks[prerequisite].append(target)
            if prerequisite not in wanted:
                wanted.append(prerequisite)
        if target not in wanted:
            wanted.append(target)

    result = LearningPlan()
    for skill in _ordered(wanted, lowered):
        entry = learning_map.entry_for(skill)
        step = LearningStep(
            skill=skill,
            demand=demand_by_skill.get(skill, 0),
            is_prerequisite=skill not in targets,
            unlocks=unlocks.get(skill, []),
            mapped=entry is not None,
        )
        if entry is not None:
            step.difficulty = entry.difficulty
            step.hours = entry.hours
            step.resources = list(entry.resources)
            result.total_hours += entry.hours
        else:
            result.unmapped.append(skill)
        result.steps.append(step)

    result.lines = _describe(result)
    return result

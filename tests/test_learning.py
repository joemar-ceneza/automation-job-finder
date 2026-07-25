"""
Tests for Standard-mode learning recommendations.

The plan must order by real demand, never recommend what the resume already
evidences (alias-aware, or it sends you to learn what you know), pull in the
foundations you lack, and put those foundations before the thing that needs
them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import learning

RESUME = ("Python developer. Skills: Python, React.js, PostgreSQL, "
          "Playwright, Git, Linux.")


def demand(**pairs) -> list[dict]:
    return [{"skill": skill, "demand": count} for skill, count in pairs.items()]


# ======================================================
# WHAT GETS RECOMMENDED
# ======================================================
def test_missing_skills_are_ranked_by_demand():
    rows = [{"skill": "Kafka", "demand": 5}, {"skill": "Docker", "demand": 40}]
    result = learning.plan(RESUME, rows, limit=5)
    names = [step.skill for step in result.steps]
    assert names.index("Docker") < names.index("Kafka")


def test_a_skill_the_resume_has_is_not_recommended():
    rows = [{"skill": "Python", "demand": 99}, {"skill": "Docker", "demand": 5}]
    result = learning.plan(RESUME, rows, limit=5)
    assert "Python" not in [step.skill for step in result.steps]


def test_the_have_check_is_alias_aware():
    """The corpus says 'React JS'; the resume says 'React.js'. Same skill."""
    rows = [{"skill": "React JS", "demand": 50}]
    result = learning.plan(RESUME, rows, limit=5)
    assert "React JS" not in [step.skill for step in result.steps]


def test_the_limit_caps_the_targets():
    rows = [{"skill": name, "demand": count} for name, count in
            [("MongoDB", 9), ("Redis", 8), ("Jest", 7), ("Cypress", 6),
             ("Figma", 5), ("Tableau", 4)]]
    result = learning.plan(RESUME, rows, limit=2)
    # Two targets, none of which have unmet prerequisites here.
    assert len(result.steps) == 2


# ======================================================
# PREREQUISITES
# ======================================================
def test_a_missing_prerequisite_is_pulled_in_and_ordered_first():
    """Kubernetes needs Docker; the resume has neither."""
    result = learning.plan(RESUME, demand(Kubernetes=30), limit=5)
    names = [step.skill for step in result.steps]
    assert "Docker" in names
    assert names.index("Docker") < names.index("Kubernetes")


def test_a_pulled_in_prerequisite_is_marked_as_one():
    result = learning.plan(RESUME, demand(Kubernetes=30), limit=5)
    docker = next(step for step in result.steps if step.skill == "Docker")
    assert docker.is_prerequisite is True
    assert "Kubernetes" in docker.unlocks


def test_a_prerequisite_you_already_have_is_not_added():
    """Docker needs Linux, which this resume already evidences."""
    result = learning.plan(RESUME, demand(Docker=30), limit=5)
    assert "Linux" not in [step.skill for step in result.steps]


def test_a_target_is_not_marked_as_a_prerequisite():
    result = learning.plan(RESUME, demand(Docker=30), limit=5)
    docker = next(step for step in result.steps if step.skill == "Docker")
    assert docker.is_prerequisite is False


def test_a_deep_chain_is_ordered_end_to_end():
    """Terraform needs AWS, AWS needs Linux (which the resume has)."""
    result = learning.plan("Python developer.", demand(Terraform=20), limit=5)
    names = [step.skill for step in result.steps]
    assert names.index("Linux") < names.index("AWS") < names.index("Terraform")


# ======================================================
# ESTIMATES AND RESOURCES
# ======================================================
def test_hours_are_summed_across_the_plan():
    result = learning.plan(RESUME, demand(Kubernetes=30), limit=5)
    expected = sum(step.hours for step in result.steps if step.hours)
    assert result.total_hours == expected > 0


def test_curated_resources_are_attached():
    result = learning.plan(RESUME, demand(Docker=30), limit=5)
    docker = next(step for step in result.steps if step.skill == "Docker")
    assert docker.resources
    assert all(url.startswith("http") for _label, url in docker.resources)


def test_an_unmapped_skill_still_appears_without_an_estimate():
    result = learning.plan(RESUME, demand(Blender=12), limit=5)
    step = next(step for step in result.steps if step.skill == "Blender")
    assert step.mapped is False
    assert step.hours is None
    assert "Blender" in result.unmapped


def test_an_empty_corpus_says_so_cleanly():
    result = learning.plan(RESUME, [], limit=5)
    assert result.steps == []
    assert result.lines  # still renders a readable message

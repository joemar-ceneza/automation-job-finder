"""
resume_parser.py
Extracts text from a PDF resume and matches it against a list of
skills/keywords, honoring the alternate spellings in config.SKILL_ALIASES.
"""
import logging
import re
import sys

import pdfplumber

import config


def extract_text_from_pdf(pdf_path: str) -> str:
    """Pull all text out of a PDF resume."""
    text_chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)
    return "\n".join(text_chunks)


def load_skills(skills_path: str) -> list[str]:
    """
    Load skills/keywords from a plain text file, one per line.
    Lines starting with # are comments; duplicate entries are dropped
    (case-insensitive) so a repeated skill can't be double-counted in scoring.
    """
    skills = []
    seen_lower = set()
    with open(skills_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower() in seen_lower:
                continue
            seen_lower.add(line.lower())
            skills.append(line)
    return skills


def skill_in_text(skill: str, text_lower: str) -> bool:
    """
    Case-insensitive whole-word/phrase match of a skill — or any of its
    aliases from config.SKILL_ALIASES — in already-lowercased text.
    """
    for term in [skill] + config.SKILL_ALIASES.get(skill, []):
        pattern = r"\b" + re.escape(term.lower()) + r"\b"
        if re.search(pattern, text_lower):
            return True
    return False


def find_matching_skills(resume_text: str, skills: list[str]) -> list[str]:
    """
    Matches each skill (including its aliases) against the resume text.
    Returns the skills that were found.
    """
    text_lower = resume_text.lower()
    return [skill for skill in skills if skill_in_text(skill, text_lower)]


# ======================================================
# SKILLS DRAFT GENERATION (--generate-skills)
# ======================================================
_SECTION_HEADINGS = re.compile(
    r"^\s*(?:technical\s+|core\s+)?(?:skills?|technologies|tech\s+stack)\b[\s:&|]*(?:and\s+tools)?\s*$",
    re.IGNORECASE)
_NEXT_SECTION = re.compile(
    r"^\s*(experience|education|work history|employment|projects?|"
    r"certifications?|references?|summary|about)\b", re.IGNORECASE)
_TOKEN_SPLIT = re.compile(r"[,;|•·•\n/]+")


def _extract_skills_section_tokens(resume_text: str) -> list[str]:
    """
    Finds the resume's skills/technologies section and splits its contents
    into candidate skill tokens. Returns [] when no such section exists.
    """
    lines = resume_text.splitlines()
    collected = []
    inside_section = False
    for line in lines:
        if _SECTION_HEADINGS.match(line):
            inside_section = True
            continue
        if inside_section:
            if not line.strip() and collected:
                break  # blank line after content ends the section
            if _NEXT_SECTION.match(line) or len(collected) >= 15:
                break
            if line.strip():
                collected.append(line)

    tokens = []
    seen_lower = set()
    for raw_token in _TOKEN_SPLIT.split("\n".join(collected)):
        token = raw_token.strip(" \t-–—:()")
        if (2 <= len(token) <= 40 and not token.isdigit()
                and token.lower() not in seen_lower):
            seen_lower.add(token.lower())
            tokens.append(token)
    return tokens


def generate_skills_draft(resume_text: str) -> tuple[list[str], list[str]]:
    """
    Drafts a skill list from the resume: returns (dictionary_hits, extras).
    dictionary_hits are config.MASTER_SKILLS entries found in the resume
    (alias-aware); extras are tokens from the resume's skills section that
    the dictionary didn't already cover — they need manual review.
    """
    text_lower = resume_text.lower()
    hits = [skill for skill in config.MASTER_SKILLS
            if skill_in_text(skill, text_lower)]

    covered = {skill.lower() for skill in hits}
    for skill in hits:
        covered.update(alias.lower() for alias in config.SKILL_ALIASES.get(skill, []))
    extras = [token for token in _extract_skills_section_tokens(resume_text)
              if token.lower() not in covered]
    return hits, extras


def write_skills_draft(hits: list[str], extras: list[str], out_path: str) -> None:
    """
    Writes the drafted skill list to out_path (never touches skills.txt
    itself — review the draft, edit it, then replace skills.txt with it).
    """
    lines = ["# Draft skills generated from your resume — review before using!",
             "# Keep the lines that truly reflect your skills, delete the rest,",
             "# then replace skills.txt with this file.", ""]
    lines += hits
    if extras:
        lines += ["", "# From your resume's skills section — not in the built-in",
                  "# dictionary, so double-check spelling/usefulness:"]
        lines += extras
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logging.info("Wrote %d dictionary skills and %d extra candidates to %s",
                 len(hits), len(extras), out_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    if len(sys.argv) != 3:
        logging.error("Usage: python resume_parser.py <resume.pdf> <skills.txt>")
        sys.exit(1)

    resume_path, skills_path = sys.argv[1], sys.argv[2]
    text = extract_text_from_pdf(resume_path)
    skills_list = load_skills(skills_path)
    found = find_matching_skills(text, skills_list)

    logging.info("Extracted %d characters from resume.", len(text))
    logging.info("Matched %d/%d skills:", len(found), len(skills_list))
    for matched_skill in found:
        logging.info("  - %s", matched_skill)

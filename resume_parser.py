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

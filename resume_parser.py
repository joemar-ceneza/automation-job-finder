"""
resume_parser.py
Extracts text from a PDF resume and matches it against a list of skills/keywords.
"""
import re
import sys
import pdfplumber


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
    Lines starting with # are treated as comments and ignored.
    """
    skills = []
    with open(skills_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                skills.append(line)
    return skills


def find_matching_skills(resume_text: str, skills: list[str]) -> list[str]:
    """
    Case-insensitive whole-word/phrase match of each skill against resume text.
    Returns the skills that were found.
    """
    text_lower = resume_text.lower()
    matched = []
    for skill in skills:
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, text_lower):
            matched.append(skill)
    return matched


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python resume_parser.py <resume.pdf> <skills.txt>")
        sys.exit(1)

    resume_path, skills_path = sys.argv[1], sys.argv[2]
    text = extract_text_from_pdf(resume_path)
    skills_list = load_skills(skills_path)
    found = find_matching_skills(text, skills_list)

    print(f"Extracted {len(text)} characters from resume.")
    print(f"Matched {len(found)}/{len(skills_list)} skills:")
    for s in found:
        print(f"  - {s}")

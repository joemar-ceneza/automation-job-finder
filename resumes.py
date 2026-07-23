"""
resumes.py
The set of resumes you maintain, and which one is in use.

Each resume is one Markdown file in resumes/, named by its stem: resumes/
backend.md is the resume called "backend". The filesystem is the source of
truth rather than a database table — a resume is a document you edit, and
keeping the text in one place removes any chance of the two drifting apart.
The chosen default is the only thing worth persisting, so it lives in meta.
"""
import logging
import os
import shutil
from dataclasses import dataclass

import config
import db_handler
import resume_model
from resume_model import MasterResume

_DEFAULT_META_KEY = "default_resume"


@dataclass(frozen=True)
class ResumeRef:
    """One resume on disk."""
    name: str
    path: str

    def load(self) -> MasterResume:
        return resume_model.load(self.path)


# ======================================================
# INTERNAL HELPERS
# ======================================================
def _path_for(name: str) -> str:
    return os.path.join(config.RESUMES_DIR, f"{name}.md")


def _migrate_legacy_master() -> None:
    """
    Moves a pre-multi-resume master_resume.md into resumes/ once.
    Copies rather than moves, so nothing of yours disappears; the old file is
    simply no longer read.
    """
    legacy = config.MASTER_RESUME_FILE
    target = _path_for(config.DEFAULT_RESUME_NAME)
    if not os.path.exists(legacy) or os.path.exists(target):
        return
    os.makedirs(config.RESUMES_DIR, exist_ok=True)
    shutil.copy2(legacy, target)
    logging.info("Copied %s to %s — resumes now live in %s, and the old file "
                 "is no longer read. Delete it whenever you like.",
                 os.path.basename(legacy), target, config.RESUMES_DIR)


# ======================================================
# PUBLIC API
# ======================================================
def available() -> list[ResumeRef]:
    """Every resume on disk, alphabetically."""
    _migrate_legacy_master()
    if not os.path.isdir(config.RESUMES_DIR):
        return []
    return [
        ResumeRef(name=os.path.splitext(entry)[0],
                  path=os.path.join(config.RESUMES_DIR, entry))
        for entry in sorted(os.listdir(config.RESUMES_DIR))
        if entry.endswith(".md")
    ]


def names() -> list[str]:
    return [ref.name for ref in available()]


def get(name: str) -> ResumeRef | None:
    """One resume by name, or None when it does not exist."""
    return next((ref for ref in available() if ref.name == name), None)


def default_name() -> str | None:
    """
    The resume used when none is named. Falls back to the only one present,
    or to the conventional name, so a fresh install needs no configuration.
    """
    existing = names()
    if not existing:
        return None
    stored = db_handler.get_meta(_DEFAULT_META_KEY)
    if stored in existing:
        return stored
    if config.DEFAULT_RESUME_NAME in existing:
        return config.DEFAULT_RESUME_NAME
    return existing[0]


def set_default(name: str) -> bool:
    """Records which resume to use by default."""
    if get(name) is None:
        logging.error("No resume called '%s'. Available: %s",
                      name, ", ".join(names()) or "none")
        return False
    db_handler.set_meta(_DEFAULT_META_KEY, name)
    logging.info("Default resume set to '%s'.", name)
    return True


def resolve(name: str | None) -> ResumeRef | None:
    """
    The resume to work with: the one named, or the default.
    Logs what is wrong rather than raising, so callers can just check None.
    """
    if name:
        ref = get(name)
        if ref is None:
            logging.error("No resume called '%s'. Available: %s",
                          name, ", ".join(names()) or "none")
        return ref

    chosen = default_name()
    if chosen is None:
        logging.error("No resumes found in %s. Create one with: "
                      "python main.py <resume.pdf> --import-resume",
                      config.RESUMES_DIR)
        return None
    return get(chosen)


def save(name: str, resume: MasterResume) -> str:
    """Writes a resume under the given name and returns its path."""
    path = _path_for(name)
    resume_model.save(resume, path)
    logging.info("Wrote resume '%s' to %s", name, path)
    return path

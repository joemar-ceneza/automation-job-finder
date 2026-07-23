"""
dashboard.py
Local Streamlit dashboard for browsing scored jobs and recording what you
did with them (interested/applied/rejected) — no terminal commands needed.

Run with:
    streamlit run dashboard.py

Reads and writes ONLY the local SQLite database (output/jobs.db). Scraping
still happens via main.py; run that (or schedule it) to refresh the data.
"""
import os

import pandas as pd
import streamlit as st

import config
import cover_letter
import db_handler
import documents
import explain
import optimizer
import resume_model
import resume_parser
import stages

_TABLE_COLUMNS = ["status", "score_percent", "title", "company", "location",
                  "source", "work_arrangement", "salary", "listing_date",
                  "matched_skills", "first_seen", "url"]

# Cards rendered per board column before collapsing to a count.
_BOARD_CARD_LIMIT = 8


# ======================================================
# DATA LOADING / FILTERING
# ======================================================
def _load_jobs(include_archived: bool) -> pd.DataFrame:
    """Loads stored jobs into a DataFrame, newest/highest score first."""
    db_handler.init_db()
    rows = db_handler.fetch_all_jobs(include_archived=include_archived)
    if not rows:
        return pd.DataFrame(columns=["job_key"] + _TABLE_COLUMNS)
    frame = pd.DataFrame(rows)
    # Normalise legacy values ('new', 'no answer') to current stage names so
    # the board, filters, and metrics all agree on what a row means.
    frame["status"] = frame["status"].map(lambda value: str(stages.parse(value)))
    frame["score_percent"] = frame["score_percent"].fillna(0)
    return frame.sort_values("score_percent", ascending=False)


def _apply_filters(frame: pd.DataFrame, search_text: str, statuses: list[str],
                   sources: list[str], min_score: float, min_salary: int,
                   hide_duplicates: bool = True) -> pd.DataFrame:
    """Applies the sidebar filters to the jobs DataFrame."""
    if hide_duplicates and "duplicate_of" in frame.columns:
        frame = frame[frame["duplicate_of"].isna()]
    if search_text:
        needle = search_text.lower()
        frame = frame[
            frame["title"].str.lower().str.contains(needle, na=False)
            | frame["company"].str.lower().str.contains(needle, na=False)
            | frame["matched_skills"].str.lower().str.contains(needle, na=False)
        ]
    if statuses:
        frame = frame[frame["status"].isin(statuses)]
    if sources:
        frame = frame[frame["source"].isin(sources)]
    if min_score > 0:
        frame = frame[frame["score_percent"] >= min_score]
    if min_salary > 0:
        frame = frame[frame["salary_max"].fillna(0) >= min_salary]
    return frame


# ======================================================
# UI SECTIONS
# ======================================================
def _render_sidebar(frame: pd.DataFrame) -> dict:
    """Renders the filter sidebar and returns the chosen filter values."""
    st.sidebar.header("Filters")
    known_statuses = [str(stage) for stage in stages.BOARD_ORDER]
    # Show every configured site, not just ones already in the database.
    known_sources = sorted(set(config.DEFAULT_SITES)
                           | {source for source in frame["source"].dropna().unique()
                              if source})
    return {
        "search_text": st.sidebar.text_input("Search title/company/skills"),
        "statuses": st.sidebar.multiselect("Status", known_statuses),
        "sources": st.sidebar.multiselect("Site", known_sources),
        "min_score": st.sidebar.slider("Minimum score %", 0.0, 100.0, 0.0, 0.5),
        "min_salary": st.sidebar.number_input(
            "Minimum salary (PHP/month, 0 = off)", min_value=0, step=5000),
        "include_archived": st.sidebar.checkbox("Include archived jobs"),
        "hide_duplicates": st.sidebar.checkbox(
            "Hide repeat postings", value=True,
            help="Same role posted more than once by the same employer. "
                 "Hidden, never deleted — a repost can mean it is still open."),
    }


def _render_metrics(frame: pd.DataFrame) -> None:
    """Shows the application funnel above the table."""
    counts = frame["status"].value_counts()

    def total(*stage_set) -> int:
        return int(sum(counts.get(str(stage), 0) for stage in stage_set))

    interviewing = total(*(stage for stage in stages.AWAITING_REPLY
                           if stage is not stages.Stage.APPLIED))
    applied = total(stages.Stage.APPLIED) + interviewing
    columns = st.columns(5)
    columns[0].metric("Tracked", len(frame))
    columns[1].metric("Saved", total(stages.Stage.SAVED,
                                     stages.Stage.INTERESTED))
    columns[2].metric("Applied", applied)
    columns[3].metric("Interviewing", interviewing)
    columns[4].metric("Offers", total(stages.Stage.OFFER,
                                      stages.Stage.ACCEPTED))


def _render_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Renders the editable jobs table; returns the edited DataFrame."""
    status_options = sorted({str(stage) for stage in stages.BOARD_ORDER}
                            | set(frame["status"].dropna().unique()))
    return st.data_editor(
        frame[["job_key"] + _TABLE_COLUMNS],
        column_config={
            "job_key": None,  # hidden — used to map edits back to the db
            "status": st.column_config.SelectboxColumn(
                "Status", options=status_options, required=True),
            "score_percent": st.column_config.NumberColumn(
                "Score %", format="%.1f", disabled=True),
            "title": st.column_config.TextColumn("Job", disabled=True),
            "company": st.column_config.TextColumn("Company", disabled=True),
            "location": st.column_config.TextColumn("Location", disabled=True),
            "source": st.column_config.TextColumn("Site", disabled=True),
            "work_arrangement": st.column_config.TextColumn(
                "Setup", disabled=True),
            "salary": st.column_config.TextColumn("Salary", disabled=True),
            "listing_date": st.column_config.TextColumn(
                "Posted", disabled=True),
            "matched_skills": st.column_config.TextColumn(
                "Matched skills", disabled=True),
            "first_seen": st.column_config.TextColumn(
                "First seen", disabled=True),
            "url": st.column_config.LinkColumn(
                "Link", display_text="open", disabled=True),
        },
        hide_index=True,
        width="stretch",
        key="jobs_table",
    )


def _save_status_changes(original: pd.DataFrame, edited: pd.DataFrame) -> None:
    """
    Persists status edits through the stage machine, so an illegal move is
    refused here exactly as it would be from the board or the CLI.
    """
    merged = original[["job_key", "status"]].merge(
        edited[["job_key", "status"]], on="job_key", suffixes=("_old", "_new"))
    changed = merged[merged["status_old"] != merged["status_new"]]
    if changed.empty:
        st.info("No status changes to save.")
        return

    saved, refused = 0, []
    for _, row in changed.iterrows():
        if db_handler.record_stage(row["job_key"], row["status_new"]):
            saved += 1
        else:
            current = stages.parse(row["status_old"])
            allowed = ", ".join(stages.allowed_moves(current)) or "nothing"
            refused.append(f"{row['status_old']} to {row['status_new']} "
                           f"(allowed: {allowed})")
    if saved:
        st.success(f"Saved {saved} stage change(s).")
    for message in refused:
        st.warning(f"Refused {message}")
    if saved:
        st.rerun()


# ======================================================
# BOARD
# ======================================================
def _render_stalled_notice() -> None:
    """Offers to mark applications the employer has gone quiet on."""
    waiting = db_handler.stalled_jobs()
    if not waiting:
        return
    with st.expander(f"{len(waiting)} application(s) with no reply in "
                     f"{config.GHOSTED_AFTER_DAYS}+ days", expanded=False):
        st.caption("Nobody remembers to record a silence. Mark these ghosted "
                   "so your response rate stays honest.")
        for job in waiting:
            columns = st.columns([6, 2, 2])
            columns[0].write(f"**{job['title']}** — {job['company'] or '—'}")
            columns[1].caption(f"{job['status']} since "
                               f"{(job['status_changed_at'] or '')[:10]}")
            if columns[2].button("Mark ghosted", key=f"ghost_{job['job_key']}"):
                db_handler.record_stage(job["job_key"], str(stages.Stage.GHOSTED),
                                        note="No reply — auto-suggested")
                st.rerun()


def _render_board(frame: pd.DataFrame) -> None:
    """Stage columns with an advance control on each card."""
    st.caption("Every application by stage. Move one with the dropdown on its "
               "card — only legal transitions are offered.")
    _render_stalled_notice()

    frame = frame.copy()
    frame["stage"] = frame["status"].map(stages.parse)
    populated = [stage for stage in stages.BOARD_ORDER
                 if (frame["stage"] == stage).any()]
    if not populated:
        st.info("Nothing tracked yet.")
        return

    for column, stage in zip(st.columns(len(populated)), populated):
        cards = frame[frame["stage"] == stage].sort_values(
            "score_percent", ascending=False)
        with column:
            st.markdown(f"**{str(stage).title()}**  \n`{len(cards)}`")
            for _, job in cards.head(_BOARD_CARD_LIMIT).iterrows():
                with st.container(border=True):
                    st.markdown(f"**{job['title'][:46]}**")
                    st.caption(f"{job['score_percent']:.0f}% · "
                               f"{job['company'] or '—'}")
                    moves = stages.allowed_moves(stage)
                    if not moves:
                        continue
                    choice = st.selectbox(
                        "Move to", ["—", *[str(move) for move in moves]],
                        key=f"move_{job['job_key']}",
                        label_visibility="collapsed")
                    if choice != "—":
                        db_handler.record_stage(job["job_key"], choice)
                        st.rerun()
            if len(cards) > _BOARD_CARD_LIMIT:
                st.caption(f"+{len(cards) - _BOARD_CARD_LIMIT} more")


# ======================================================
# ANALYTICS
# ======================================================
def _render_analytics() -> None:
    """Skill demand across every stored job — no AI involved."""
    total = db_handler.total_active_jobs()
    if not total:
        st.info("No jobs stored yet.")
        return

    overall = db_handler.skill_demand(limit=15)
    if not overall:
        st.info("No skills extracted yet. Run main.py (or --rescore) to "
                "populate skill demand.")
        return

    st.caption(f"What {total} tracked advertisements ask for. Counts are exact; "
               "percentages appear once the corpus is large enough to mean "
               "something.")
    # A bar column rather than st.bar_chart: the chart sorts by index, which
    # would list these alphabetically and bury the most-demanded skill.
    demand = pd.DataFrame(overall)[["skill", "category", "demand", "in_title"]]
    st.dataframe(
        demand,
        column_config={
            "skill": st.column_config.TextColumn("Skill"),
            "category": st.column_config.TextColumn("Category", width="small"),
            "demand": st.column_config.ProgressColumn(
                "Jobs asking for it", format="%d",
                min_value=0, max_value=int(demand["demand"].max())),
            "in_title": st.column_config.NumberColumn(
                "In title", width="small",
                help="Mentions in the job title, which weigh triple"),
        },
        hide_index=True, width="stretch")

    st.subheader("By category")
    categories = ["language", "framework", "database", "cloud", "ai", "tool"]
    for row_categories in (categories[:3], categories[3:]):
        for column, category in zip(st.columns(3), row_categories):
            rows = db_handler.skill_demand(category=category, limit=8)
            with column:
                st.markdown(f"**{category.title()}**")
                if not rows:
                    st.caption("Nothing found yet.")
                    continue
                for row in rows:
                    share = (f" · {round(row['demand'] / total * 100)}%"
                             if total >= config.CALIBRATION_MIN_JOBS
                             and row["demand"] / total >= 0.01 else "")
                    st.caption(f"`{row['demand']:>3}` {row['skill']}{share}")


# ======================================================
# JOB DETAIL
# ======================================================
@st.cache_data(show_spinner=False)
def _load_master_resume(mtime: float):
    """
    Reads the master resume, keyed by file mtime so edits are picked up
    without restarting Streamlit.
    """
    resume = resume_model.load(config.MASTER_RESUME_FILE)
    skills = resume_parser.find_matching_skills(
        resume.full_text(), resume_parser.load_skills(config.DEFAULT_SKILLS_FILE))
    return resume, skills


def _master_resume():
    """The resume and its matched skills, or (None, None) when absent."""
    if not os.path.exists(config.MASTER_RESUME_FILE):
        return None, None
    return _load_master_resume(os.path.getmtime(config.MASTER_RESUME_FILE))


def _remember(slot: str, job_key: str, paths: list[str]) -> None:
    """Records generated files against the job they belong to."""
    st.session_state[slot] = {"job_key": job_key, "paths": paths}


def _offer_downloads(slot: str, job_key: str) -> None:
    """
    One download button per generated file, but only for the job currently
    selected. Without the job check the buttons linger after switching jobs
    and quietly offer the previous job's documents — which is how someone
    sends the wrong cover letter.
    """
    stored = st.session_state.get(slot)
    if not stored or stored["job_key"] != job_key:
        return
    for column, path in zip(st.columns(len(stored["paths"])),
                            stored["paths"]):
        if not os.path.exists(path):
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        column.download_button(
            f"Download {os.path.splitext(path)[1].lstrip('.').upper()}",
            data=data, file_name=os.path.basename(path),
            key=f"{slot}_{path}", width="stretch")


def _render_score_explanation(job: dict, resume_skills: list[str],
                              resume_text: str = "") -> None:
    """Why this job scored what it scored — deterministic, no AI."""
    result = explain.explain_job(job, resume_skills, resume_text)
    for line in result.lines:
        st.markdown(f"- {line}")

    if result.title_matches or result.body_matches:
        st.caption("Matched skills")
        chips = ([f"**{skill}** (title)" for skill in result.title_matches]
                 + list(result.body_matches))
        st.markdown(" · ".join(chips))


def _render_tailor(job: dict, resume) -> None:
    """Standard-mode resume optimiser with export."""
    result = optimizer.optimise(resume, job)

    left, right = st.columns([1, 2])
    left.metric("ATS score", f"{result.ats_score:.0f}/100")
    with right:
        for change in result.changes:
            st.markdown(f"- {change}")

    with st.expander("ATS breakdown"):
        for check in result.checks:
            share = check.points / check.max_points if check.max_points else 0
            st.markdown(f"**{check.name}** — {check.points:.1f} / "
                        f"{check.max_points:.0f}")
            st.progress(min(1.0, share))
            st.caption(check.detail)

    if st.button("Generate tailored resume", key="tailor_go",
                 type="primary", width="stretch"):
        stem = documents.slugify(f"{job['title']}-{job.get('company') or ''}")
        paths = [documents.write(result.resume,
                                 os.path.join(config.DOCUMENTS_DIR,
                                              f"{stem}.{fmt}"), fmt)
                 for fmt in config.DOCUMENT_FORMATS]
        _remember("tailor_files", job["job_key"], paths)
        st.success(f"Written to {config.DOCUMENTS_DIR}")

    _offer_downloads("tailor_files", job["job_key"])


def _render_cover_letter(job: dict, resume) -> None:
    """Standard-mode cover letter with export."""
    tones = cover_letter.available_tones()
    if not tones:
        st.warning(f"No templates found in {config.COVER_LETTER_TEMPLATE_DIR}.")
        return

    controls = st.columns([1, 1])
    tone = controls[0].selectbox(
        "Tone", tones,
        index=tones.index(config.COVER_LETTER_TONE)
        if config.COVER_LETTER_TONE in tones else 0)
    recipient = controls[1].text_input(
        "Addressed to", value=config.COVER_LETTER_RECIPIENT)

    letter = cover_letter.compose(resume, job, tone=tone, recipient=recipient)
    st.text_area("Draft", letter.to_text(), height=340,
                 label_visibility="collapsed")
    st.caption("Read it before sending — a template letter reads like one, "
               "and the opening line is usually worth rewriting yourself.")

    if st.button("Save cover letter", key="letter_go", type="primary",
                 width="stretch"):
        stem = documents.slugify(
            f"cover-letter-{job['title']}-{job.get('company') or ''}")
        paths = [documents.write_letter(letter,
                                        os.path.join(config.DOCUMENTS_DIR,
                                                     f"{stem}.{fmt}"), fmt)
                 for fmt in config.DOCUMENT_FORMATS]
        _remember("letter_files", job["job_key"], paths)
        st.success(f"Written to {config.DOCUMENTS_DIR}")

    _offer_downloads("letter_files", job["job_key"])


def _render_stage_control(job: dict) -> None:
    """Move the application and record a note, from the detail view."""
    current = stages.parse(job.get("status"))
    moves = stages.allowed_moves(current)

    columns = st.columns([1, 2])
    columns[0].markdown(f"Stage  \n**{str(current).title()}**")
    if moves:
        choice = columns[1].selectbox(
            "Move to", ["—", *[str(move) for move in moves]],
            key=f"detail_move_{job['job_key']}")
        if choice != "—":
            db_handler.record_stage(job["job_key"], choice)
            st.rerun()
    else:
        columns[1].caption("This stage is final.")

    note = st.text_area("Notes", value=job.get("notes") or "", height=90,
                        key=f"note_{job['job_key']}")
    if st.button("Save note", key=f"save_note_{job['job_key']}"):
        db_handler.set_note(job["job_key"], note)
        st.success("Note saved.")

    history = db_handler.stage_history(job["job_key"])
    if history:
        with st.expander(f"History ({len(history)})"):
            for event in history:
                st.caption(f"{event['occurred_at'][:16]} — {event['stage']}"
                           + (f" · {event['note']}" if event["note"] else ""))


def _render_job_detail(frame: pd.DataFrame) -> None:
    """Everything about one job: why it scored, and what to send."""
    if frame.empty:
        st.info("No jobs match the current filters.")
        return

    options = frame["job_key"].tolist()
    labels = {
        row["job_key"]: f"{row['score_percent']:.0f}%  {row['title'][:60]}"
                        f"  ·  {row['company'] or '—'}"
        for _, row in frame.iterrows()
    }
    job_key = st.selectbox("Job", options, format_func=lambda key: labels[key])
    job = db_handler.get_job(job_key)
    if job is None:
        st.error("That job is no longer in the database.")
        return

    st.markdown(f"### {job['title']}")
    st.caption(f"{job.get('company') or 'Employer not published'} · "
               f"{job.get('location') or '—'} · {job.get('source')} · "
               f"{job.get('salary') or 'No salary stated'}")
    if job.get("url"):
        st.markdown(f"[Open the original posting]({job['url']})")

    _render_stage_control(job)
    st.divider()

    resume, resume_skills = _master_resume()
    if resume is None:
        st.info("No master resume yet. Create one to unlock tailoring and "
                "cover letters:\n\n"
                "`python main.py resume.pdf --import-resume`")
        st.subheader("Why this score")
        _render_score_explanation(job, [])
        return

    score_tab, tailor_tab, letter_tab = st.tabs(
        ["Why this score", "Tailor resume", "Cover letter"])
    with score_tab:
        _render_score_explanation(job, resume_skills, resume.full_text())
    with tailor_tab:
        _render_tailor(job, resume)
    with letter_tab:
        _render_cover_letter(job, resume)


# ======================================================
# PAGE
# ======================================================
def run_dashboard() -> None:
    """Entry point — renders the whole dashboard page."""
    st.set_page_config(page_title="Job Matcher Dashboard", page_icon="🧭",
                       layout="wide")
    st.title("Job Matcher Dashboard")
    st.caption(f"Data: {config.DB_PATH} — refresh it by running main.py")

    frame = _load_jobs(include_archived=st.session_state.get(
        "include_archived_value", False))
    if frame.empty:
        st.warning("No jobs in the database yet. Run main.py first.")
        return

    filters = _render_sidebar(frame)
    st.session_state["include_archived_value"] = filters["include_archived"]

    filtered = _apply_filters(frame, filters["search_text"],
                              filters["statuses"], filters["sources"],
                              filters["min_score"], filters["min_salary"],
                              filters["hide_duplicates"])
    _render_metrics(filtered)

    if filters["hide_duplicates"]:
        # Compare against the same filters minus the duplicate rule, so the
        # count reflects duplicates only and not every other exclusion.
        with_duplicates = _apply_filters(
            frame, filters["search_text"], filters["statuses"],
            filters["sources"], filters["min_score"], filters["min_salary"],
            hide_duplicates=False)
        hidden = len(with_duplicates) - len(filtered)
        if hidden > 0:
            st.caption(f"{hidden} repeat posting(s) hidden — untick "
                       "*Hide repeat postings* in the sidebar to see them.")

    matches_tab, detail_tab, board_tab, analytics_tab = st.tabs(
        ["Matches", "Job detail", "Board", "Skill demand"])

    with matches_tab:
        st.caption("Change any row's Status, then click Save.")
        edited = _render_table(filtered.reset_index(drop=True))
        if st.button("Save status changes", type="primary"):
            _save_status_changes(filtered.reset_index(drop=True), edited)

    with detail_tab:
        _render_job_detail(filtered.reset_index(drop=True))

    with board_tab:
        _render_board(frame)

    with analytics_tab:
        _render_analytics()


run_dashboard()

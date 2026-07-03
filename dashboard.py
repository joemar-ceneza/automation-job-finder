"""
dashboard.py
Local Streamlit dashboard for browsing scored jobs and recording what you
did with them (interested/applied/rejected) — no terminal commands needed.

Run with:
    streamlit run dashboard.py

Reads and writes ONLY the local SQLite database (output/jobs.db). Scraping
still happens via main.py; run that (or schedule it) to refresh the data.
"""
import pandas as pd
import streamlit as st

import config
import db_handler

_TABLE_COLUMNS = ["status", "score_percent", "title", "company", "location",
                  "source", "work_arrangement", "salary", "listing_date",
                  "matched_skills", "first_seen", "url"]


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
    frame["status"] = frame["status"].fillna("new").replace("", "new")
    frame["score_percent"] = frame["score_percent"].fillna(0)
    return frame.sort_values("score_percent", ascending=False)


def _apply_filters(frame: pd.DataFrame, search_text: str, statuses: list[str],
                   sources: list[str], min_score: float,
                   min_salary: int) -> pd.DataFrame:
    """Applies the sidebar filters to the jobs DataFrame."""
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
    known_statuses = sorted(set(config.STATUS_OPTIONS)
                            | set(frame["status"].dropna().unique()))
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
    }


def _render_metrics(frame: pd.DataFrame) -> None:
    """Shows headline counts above the table."""
    counts = frame["status"].value_counts()
    columns = st.columns(4)
    columns[0].metric("Total jobs", len(frame))
    columns[1].metric("New", int(counts.get("new", 0)))
    columns[2].metric("Interested", int(counts.get("interested", 0)))
    columns[3].metric("Applied", int(counts.get("applied", 0)))


def _render_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Renders the editable jobs table; returns the edited DataFrame."""
    status_options = sorted(set(config.STATUS_OPTIONS)
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
        use_container_width=True,
        key="jobs_table",
    )


def _save_status_changes(original: pd.DataFrame, edited: pd.DataFrame) -> None:
    """Persists any status edits back to the SQLite database."""
    merged = original[["job_key", "status"]].merge(
        edited[["job_key", "status"]], on="job_key", suffixes=("_old", "_new"))
    changed = merged[merged["status_old"] != merged["status_new"]]
    if changed.empty:
        st.info("No status changes to save.")
        return
    updates = dict(zip(changed["job_key"], changed["status_new"]))
    count = db_handler.update_statuses(updates)
    st.success(f"Saved {count} status change(s).")
    st.rerun()


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
                              filters["min_score"], filters["min_salary"])
    _render_metrics(filtered)

    st.caption("Change any row's Status, then click Save.")
    edited = _render_table(filtered.reset_index(drop=True))
    if st.button("Save status changes", type="primary"):
        _save_status_changes(filtered.reset_index(drop=True), edited)


run_dashboard()

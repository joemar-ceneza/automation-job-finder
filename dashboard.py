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
import subprocess
import sys

import pandas as pd
import streamlit as st

import ai_cover_letter
import ai_explain
import ai_interview
import ai_rewrite
import ai_salary
import ai_summary
import analytics
import app_settings
import config
import cover_letter
import db_handler
import documents
import explain
import interview
import llm
import optimizer
import resume_model
import resume_parser
import resumes
import salary_bands
import skill_extractor
import skill_proposals
import stages
import summary
import tracked_skills

_TABLE_COLUMNS = ["status", "score_percent", "title", "company", "location",
                  "source", "work_arrangement", "salary", "listing_date",
                  "matched_skills", "first_seen", "url"]

# Cards rendered per board column before collapsing to a count.
_BOARD_CARD_LIMIT = 8

# Sites the Run tab can search — matches main.SITE_SCRAPERS.
_SITE_OPTIONS = ["jobstreet", "onlinejobs"]


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
def _pct(value: float | None) -> str:
    """A percentage for a metric tile, or an em dash when there's no sample."""
    return f"{value:.0f}%" if value is not None else "—"


def _render_pipeline() -> None:
    """The application funnel, conversion/response rates, and weekly volume."""
    funnel = analytics.compute(db_handler.all_stage_events())
    st.subheader("Application pipeline")
    if funnel.applied == 0:
        st.caption("No applications tracked yet. Move a job to **applied** on "
                   "the Matches tab (or `--set-status`) and your funnel, "
                   "conversion rates, and weekly volume appear here.")
        st.divider()
        return

    stages_row = st.columns(4)
    stages_row[0].metric("Applied", funnel.applied)
    stages_row[1].metric("Interviewed", funnel.interviewed)
    stages_row[2].metric("Offers", funnel.offers)
    stages_row[3].metric("Accepted", funnel.accepted)

    rates_row = st.columns(4)
    resolved = funnel.responded + funnel.no_response
    rates_row[0].metric(
        "Response rate", _pct(funnel.response_rate),
        help=f"{funnel.responded} responded of {resolved} resolved · "
             f"{funnel.pending} still pending (not counted)")
    rates_row[1].metric("Applied → Interview",
                        _pct(funnel.applied_to_interview))
    rates_row[2].metric("Interview → Offer", _pct(funnel.interview_to_offer))
    rates_row[3].metric("Offer → Accept", _pct(funnel.offer_to_accept))

    if funnel.weekly_applications:
        st.caption("Applications per week")
        frame = pd.DataFrame(funnel.weekly_applications,
                             columns=["week", "applications"]).set_index("week")
        st.bar_chart(frame, height=200)
    st.divider()


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

    _render_skill_proposals()


def _apply_skill_proposal(canonical: str, category: str,
                          aliases: list[str]) -> None:
    """Approve a skill and rebuild job_skills so its demand shows immediately."""
    tracked_skills.add(canonical, category, tuple(aliases))
    jobs = db_handler.fetch_all_jobs()
    db_handler.replace_job_skills(
        skill_extractor.extract_for_rows(jobs, tracked_skills.additions()))


def _render_skill_proposals() -> None:
    """Feature 16 — suggest in-demand skills the dictionary doesn't track yet."""
    proposals = skill_proposals.propose(
        db_handler.fetch_all_jobs(), min_occurrences=3,
        extra_tracked=tracked_skills.additions())
    st.divider()
    st.subheader("Suggested skills to track")
    if not proposals:
        st.caption("Your dictionary already covers what your corpus asks for — "
                   "nothing to suggest.")
        return
    st.caption("Skills your ads ask for that you don't track yet. Add the ones "
               "worth counting — it re-extracts your corpus so their demand "
               "shows above.")
    for proposal in proposals[:20]:
        columns = st.columns([4, 1])
        with columns[0]:
            st.markdown(f"**{proposal.canonical}** · {proposal.category}")
            st.caption(f"{proposal.rationale} Seen as: "
                       + ", ".join(proposal.merge_from))
        if columns[1].button(f"Track ({proposal.occurrences})",
                             key=f"addskill_{proposal.canonical}",
                             width="stretch"):
            with st.spinner(f"Adding {proposal.canonical} and re-extracting…"):
                _apply_skill_proposal(proposal.canonical, proposal.category,
                                      proposal.merge_from)
            st.success(f"Now tracking {proposal.canonical}.")
            st.rerun()


# ======================================================
# JOB DETAIL
# ======================================================
@st.cache_data(show_spinner=False)
def _load_resume(path: str, mtime: float):
    """
    Reads one resume, keyed by path and file mtime so edits are picked up
    without restarting Streamlit.
    """
    resume = resume_model.load(path)
    skills = resume_parser.find_matching_skills(
        resume.full_text(), resume_parser.load_skills(config.DEFAULT_SKILLS_FILE))
    return resume, skills


def _selected_resume():
    """
    The resume chosen in the sidebar and its matched skills, or (None, None)
    when none exist yet.
    """
    references = resumes.available()
    if not references:
        return None, None
    chosen = st.session_state.get("resume_name") or resumes.default_name()
    reference = resumes.get(chosen) or references[0]
    return _load_resume(reference.path, os.path.getmtime(reference.path))


def _render_resume_picker() -> None:
    """Sidebar control for which resume the document tools use."""
    references = resumes.available()
    if not references:
        return
    names = [reference.name for reference in references]
    default = resumes.default_name()
    st.sidebar.header("Resume")
    st.sidebar.selectbox(
        "In use", names,
        index=names.index(default) if default in names else 0,
        key="resume_name",
        help=f"Markdown files in {config.RESUMES_DIR}. Add one to compare "
             "versions against a job.")


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


def _render_summary(job: dict) -> None:
    """Scannable job summary — sections and red flags, with optional AI."""
    result = summary.summarise(job)

    facts = [result.work_arrangement]
    if result.salary_text:
        facts.append(result.salary_text)
    if result.required_years:
        facts.append(f"{result.required_years}+ yrs")
    st.markdown(" · ".join(facts))

    for flag in result.red_flags:
        st.warning(f"⚠ {flag}")

    labels = [("responsibilities", "What you'd do"),
              ("requirements", "What they require"),
              ("nice_to_have", "Nice to have"),
              ("benefits", "What they offer")]
    for attribute, heading in labels:
        items = getattr(result, attribute)
        if items:
            st.markdown(f"**{heading}**")
            for item in items:
                st.markdown(f"- {item}")

    if not result.has_sections():
        st.info("This advert has no clear sections to extract — read the full "
                "posting via the job title link.")

    _render_ai_summary(job, result)
    _render_salary(job)


def _render_salary(job: dict) -> None:
    """Band this job's pay against similar roles you track, with optional AI."""
    corpus = db_handler.salaried_jobs(job.get("search_keyword") or None)
    assessment = salary_bands.assess(job, corpus)

    st.divider()
    st.markdown("**Salary**")
    if not assessment.has_salary:
        st.caption("This advert states no salary.")
        return

    figures = st.columns(4)
    figures[0].metric("Monthly", f"₱{assessment.monthly:,}")
    figures[1].metric("Yearly", f"₱{assessment.yearly:,}")
    figures[2].metric("With 13th", f"₱{assessment.yearly_13th:,}")
    figures[3].metric("Hourly", f"₱{assessment.hourly:,}")

    if assessment.enough_sample:
        marker = {"Below": "🔻", "Competitive": "➖",
                  "Above": "🔺"}.get(assessment.band, "")
        st.markdown(
            f"{marker} **{assessment.band}** versus {assessment.sample_size} "
            f"“{assessment.role}” postings you track — median "
            f"₱{assessment.corpus_median:,}, middle half "
            f"₱{assessment.p25:,}–₱{assessment.p75:,}.")
    else:
        st.caption(f"Only {assessment.sample_size} “{assessment.role}” "
                   f"posting(s) with a salary tracked — too few to call it "
                   f"competitive (need {config.SALARY_MIN_SAMPLES}).")

    _render_ai_salary(job, assessment)


def _render_ai_salary(job: dict, base) -> None:
    """Optional AI reading of the pay — competitiveness, negotiation, seniority."""
    provider = llm.get_provider(db_handler)
    if not base.has_salary or not provider.is_available():
        return

    key = f"ai_salary_{job['job_key']}"
    triggered = st.button("Read the pay with AI", key=f"btn_{key}")
    if triggered or (key not in st.session_state
                     and app_settings.mode_for("salary") == "ai"):
        with st.spinner("Reading the pay…"):
            st.session_state[key] = ai_salary.enrich(
                job, base, provider, effort=config.AI_EFFORT)

    result = st.session_state.get(key)
    if result is None:
        return
    if not result.ai_used:
        if result.note:
            st.caption(f"AI read unavailable: {result.note}")
        return
    st.markdown(f"**AI read** · {result.model}"
                + (" · cached" if result.from_cache else ""))
    st.markdown(result.competitiveness)
    if result.negotiation:
        st.markdown(f"**Negotiation** — {result.negotiation}")
    if result.seniority_read:
        st.markdown(f"**Vs seniority** — {result.seniority_read}")


def _render_ai_summary(job: dict, base) -> None:
    """Optional AI reading — overview, pros/cons, growth, subtler red flags."""
    provider = llm.get_provider(db_handler)
    if not provider.is_available():
        st.caption("AI mode is off. Set a provider in `.env` for a plain-English "
                   "read with pros, cons, and subtler red flags.")
        return

    st.divider()
    key = f"ai_summary_{job['job_key']}"
    triggered = st.button("Summarise with AI", key=f"btn_{key}")
    if triggered or (key not in st.session_state
                     and app_settings.mode_for("summary") == "ai"):
        with st.spinner("Reading the advert…"):
            st.session_state[key] = ai_summary.enrich(
                job, base, provider, effort=config.AI_EFFORT)

    result = st.session_state.get(key)
    if result is None:
        return
    if not result.ai_used:
        st.warning("The AI summary was unavailable, so only the sections above "
                   "are shown."
                   + (f"\n\nReason: {result.note}" if result.note else ""))
        return

    st.markdown(f"**AI summary** · {result.model}"
                + (" · cached" if result.from_cache else ""))
    st.markdown(result.overview)
    if result.pros:
        st.markdown("**Pros** — " + "; ".join(result.pros))
    if result.cons:
        st.markdown("**Cons** — " + "; ".join(result.cons))
    if result.growth:
        st.markdown(f"**Growth** — {result.growth}")
    for flag in result.red_flags:
        st.warning(f"⚠ {flag}")


def _render_score_explanation(job: dict, resume_skills: list[str],
                              resume_text: str = "") -> None:
    """Why this job scored what it scored — deterministic, with optional AI."""
    result = explain.explain_job(job, resume_skills, resume_text)
    for line in result.lines:
        st.markdown(f"- {line}")

    if result.title_matches or result.body_matches:
        st.caption("Matched skills")
        chips = ([f"**{skill}** (title)" for skill in result.title_matches]
                 + list(result.body_matches))
        st.markdown(" · ".join(chips))

    _render_ai_narrative(job, resume_skills, resume_text)


def _render_ai_narrative(job: dict, resume_skills: list[str],
                         resume_text: str) -> None:
    """The optional AI narrative, grounded in the deterministic facts above."""
    provider = llm.get_provider(db_handler)
    if not provider.is_available():
        st.caption("AI mode is off. Set a provider in `.env` "
                   "(see `.env.example`) to add a written explanation here.")
        return

    key = f"ai_explain_{job['job_key']}"
    triggered = st.button("Explain with AI", key=f"btn_{key}")
    if triggered or (key not in st.session_state
                     and app_settings.mode_for("explain") == "ai"):
        with st.spinner("Asking the model (about 15 seconds)…"):
            st.session_state[key] = ai_explain.enrich(
                job, resume_skills, resume_text, provider,
                effort=config.AI_EFFORT)

    result = st.session_state.get(key)
    if result is None or not getattr(result, "ai_used", False):
        if result is not None:
            note = getattr(result, "note", "")
            st.warning("The AI narrative was unavailable, so only the "
                       "deterministic explanation above is shown."
                       + (f"\n\nReason: {note}" if note else ""))
        return

    st.divider()
    st.markdown(f"**AI take** · {result.model}"
                + (" · cached" if result.from_cache else ""))
    st.markdown(result.summary)
    if result.strengths:
        st.markdown("**Strengths** — " + "; ".join(result.strengths))
    if result.weaknesses:
        st.markdown("**Weak areas** — " + "; ".join(result.weaknesses))
    if result.improvements:
        st.markdown("**Do next** — " + "; ".join(result.improvements))
    if result.advice:
        st.caption(result.advice)


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
    _render_ai_rewrite(job, result.resume)


def _render_ai_rewrite(job: dict, tailored) -> None:
    """AI bullet rewriting, with the fabrication verifier's verdict shown."""
    provider = llm.get_provider(db_handler)
    if not provider.is_available():
        st.caption("AI mode is off. Set a provider in `.env` to rewrite the "
                   "wording of these bullets for the job.")
        return

    st.divider()
    st.caption("AI mode can rewrite the bullet wording for this job. It never "
               "invents: any rewrite that adds a number or skill not in your "
               "resume is rejected and your original kept.")
    key = f"rewrite_{job['job_key']}"
    if st.button("Improve wording with AI", key=f"btn_{key}"):
        with st.spinner("Rewriting and fact-checking each bullet…"):
            st.session_state[key] = ai_rewrite.rewrite_for_job(
                tailored, job, provider, effort=config.AI_EFFORT)

    result = st.session_state.get(key)
    if result is None:
        return
    if not result.ai_used:
        st.warning("The AI rewrite was unavailable — your tailored resume "
                   "above is unchanged.")
        return

    st.markdown(f"Rewrote **{result.rewritten}** bullet(s), kept "
                f"**{result.kept_original}** as written · {result.model}"
                + (" · cached" if result.from_cache else ""))
    if result.rejections:
        with st.expander(f"{len(result.rejections)} rewrite(s) rejected as "
                         "fabricated — your originals were kept"):
            for rejection in result.rejections:
                st.caption(rejection)

    stem = documents.slugify(
        f"rewritten-{job['title']}-{job.get('company') or ''}")
    if st.button("Save rewritten resume", key=f"save_{key}", type="primary",
                 width="stretch"):
        paths = [documents.write(result.resume,
                                 os.path.join(config.DOCUMENTS_DIR,
                                              f"{stem}.{fmt}"), fmt)
                 for fmt in config.DOCUMENT_FORMATS]
        _remember("rewrite_files", job["job_key"], paths)
        st.success(f"Written to {config.DOCUMENTS_DIR}. Review every line "
                   "before sending.")
    _offer_downloads("rewrite_files", job["job_key"])


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

    provider = llm.get_provider(db_handler)
    use_ai = False
    if provider.is_available():
        use_ai = st.checkbox(
            "Write the body with AI",
            value=app_settings.mode_for("cover_letter") == "ai",
            help="The model writes the letter from your real accomplishments. "
                 "Any paragraph that invents a number or skill not in your "
                 "resume is rejected and the template letter used instead. "
                 "Default set in Settings.")

    if use_ai:
        with st.spinner("Writing and fact-checking the letter…"):
            letter = ai_cover_letter.compose(
                resume, job, provider, tone=tone, recipient=recipient,
                effort=config.AI_EFFORT)
    else:
        letter = cover_letter.compose(resume, job, tone=tone,
                                      recipient=recipient)

    st.text_area("Draft", letter.to_text(), height=340,
                 label_visibility="collapsed")
    if letter.ai_used:
        st.caption(f"Body written by AI ({letter.model}"
                   + (" · cached" if letter.from_cache else "")
                   + ") and checked against your resume. Read it before "
                   "sending — the wording is the model's, the facts are yours.")
    else:
        if use_ai:
            st.warning("The AI letter couldn't be used — showing the template "
                       "letter instead.")
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


def _render_interview(job: dict, resume, resume_skills: list[str]) -> None:
    """Likely interview questions and talking points — deterministic, no AI."""
    explanation = explain.explain_job(job, resume_skills, resume.full_text())
    prep = interview.prepare(resume, job, explanation)

    if prep.strengths:
        st.markdown("**Lead with these** — the advert asks for them and your "
                    "resume shows them:")
        for point in prep.strengths:
            st.markdown(f"- **{point.skill}**"
                        + (f" — {point.bullet}" if point.bullet
                           else " — _have a concrete example ready_"))
    else:
        st.info("This advert names none of your skills. Prepare to show "
                "transferable experience and genuine interest in the role.")

    if prep.gaps:
        st.warning("Expect to be pressed on what you don't list: "
                   + ", ".join(prep.gaps) + ".")

    st.divider()
    labels = {"Experience": "Your experience", "Gap": "Filling the gaps",
              "Behavioural": "Behavioural"}
    for category, heading in labels.items():
        group = [q for q in prep.questions if q.category == category]
        if not group:
            continue
        st.markdown(f"**{heading}**")
        for question in group:
            st.markdown(f"- {question.prompt}")
            if question.hint:
                st.caption(f"→ {question.hint}")

    st.caption("These are talking points from your own resume — rehearse them "
               "in your words, don't read them.")

    _render_ai_answers(job, resume, prep)


def _render_ai_answers(job: dict, resume, prep) -> None:
    """Optional AI-drafted answers, each verified against the resume."""
    provider = llm.get_provider(db_handler)
    if not provider.is_available():
        st.caption("AI mode is off. Set a provider in `.env` to draft a full "
                   "answer for each question.")
        return

    st.divider()
    st.caption("AI mode can draft a suggested answer for each question, written "
               "from your real accomplishments. Any answer that claims a skill "
               "or number not in your resume is dropped.")
    key = f"interview_ai_{job['job_key']}"
    triggered = st.button("Draft answers with AI", key=f"btn_{key}")
    if triggered or (key not in st.session_state
                     and app_settings.mode_for("interview") == "ai"):
        with st.spinner("Drafting and fact-checking each answer…"):
            st.session_state[key] = ai_interview.enrich(
                resume, job, prep, provider, effort=config.AI_EFFORT)

    result = st.session_state.get(key)
    if result is None:
        return
    if not result.ai_used:
        st.warning("AI answers were unavailable — the talking points above "
                   "stand."
                   + (f"\n\nReason: {result.note}" if result.note else ""))
        return

    st.markdown(f"**AI-drafted answers** · {result.model}"
                + (" · cached" if result.from_cache else ""))
    for answer in result.answers:
        st.markdown(f"**{answer.prompt}**")
        st.markdown(answer.answer)
    st.caption("Rehearse these in your own words — the facts are yours, the "
               "phrasing is a starting point.")


def _render_comparison(job: dict) -> None:
    """Ranks every resume against this job — arithmetic, no AI."""
    references = resumes.available()
    if len(references) < 2:
        st.info(f"Only one resume found. Add another `.md` file to "
                f"`{config.RESUMES_DIR}` — a backend-leaning and a full-stack "
                "version, say — and this ranks them against each job.")
        return

    rankings = optimizer.compare(
        job, [(reference.name, reference.load()) for reference in references])

    st.dataframe(
        pd.DataFrame([{
            "Resume": ranking.name,
            "Overall": ranking.combined,
            "Match %": ranking.match_percent,
            "ATS": ranking.ats_score,
            "Evidenced": len(ranking.matched),
            "Missing": ", ".join(ranking.missing[:5]) or "nothing",
        } for ranking in rankings]),
        column_config={
            "Overall": st.column_config.ProgressColumn(
                "Overall", format="%.1f", min_value=0, max_value=100),
        },
        hide_index=True, width="stretch")

    best = rankings[0]
    tied = [ranking.name for ranking in rankings
            if ranking.combined == best.combined]
    if len(tied) > 1:
        st.info(f"**{' and '.join(tied)} score identically** — nothing that "
                "separates them is asked for here, so use whichever you "
                "prefer.")
    else:
        st.success(f"**Use {best.name}** — it evidences "
                   f"{len(best.matched)} of the "
                   f"{len(best.matched) + len(best.missing)} skills this "
                   f"advert names, {best.combined - rankings[1].combined:.1f} "
                   f"points ahead of {rankings[1].name}.")
    if best.unmentioned:
        st.caption("Evidenced in the experience but absent from the Skills "
                   f"section: {', '.join(best.unmentioned)}")


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

    resume, resume_skills = _selected_resume()
    if resume is None:
        st.info("No resume yet. Create one to unlock tailoring and cover "
                "letters:\n\n`python main.py resume.pdf --import-resume`")
        st.subheader("Why this score")
        _render_score_explanation(job, [])
        return

    (summary_tab, score_tab, tailor_tab, letter_tab, interview_tab,
     compare_tab) = st.tabs(
        ["Summary", "Why this score", "Tailor resume", "Cover letter",
         "Interview prep", "Compare resumes"])
    with summary_tab:
        _render_summary(job)
    with score_tab:
        _render_score_explanation(job, resume_skills, resume.full_text())
    with tailor_tab:
        _render_tailor(job, resume)
    with letter_tab:
        _render_cover_letter(job, resume)
    with interview_tab:
        _render_interview(job, resume, resume_skills)
    with compare_tab:
        _render_comparison(job)


# ======================================================
# RUN & TOOLS (parity with the main.py CLI)
# ======================================================
def _quote(arg: str) -> str:
    """Renders a command argument for display, quoting anything with spaces."""
    return f'"{arg}"' if " " in arg else arg


def _run_cli(arg_list: list[str]) -> None:
    """
    Runs `python main.py <args>` as a subprocess and streams its output live.
    This is the same code path as the terminal — the dashboard just builds the
    command from the form — so anything the CLI does is reachable from the UI.
    """
    command = [sys.executable, "main.py"] + arg_list
    st.caption("Running: python " + " ".join(_quote(part) for part in
                                              ["main.py"] + arg_list))
    output = st.empty()
    lines: list[str] = []
    try:
        process = subprocess.Popen(
            command, cwd=config.BASE_DIR, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError as error:
        st.error(f"Could not start the command: {error}")
        return

    for line in process.stdout:
        lines.append(line.rstrip())
        output.code("\n".join(lines[-500:]))
    process.wait()
    output.code("\n".join(lines[-500:]) or "(no output)")
    if process.returncode == 0:
        st.success("Done. Reload the page or switch tabs to see refreshed data.")
    else:
        st.error(f"Exited with code {process.returncode} — see the output "
                 "above.")


def _render_search_form() -> None:
    """The full scrape/score pipeline — every search flag main.py accepts."""
    st.subheader("Search for jobs")
    st.caption("Runs the same pipeline as `python main.py` — scrape the "
               "selected sites, score new jobs against your skills, and save "
               "them to the database.")

    resume_pdf = st.text_input("Resume PDF path", key="run_pdf",
                               placeholder=r"C:\path\to\resume.pdf")
    keyword = st.text_input(
        "Keywords (comma-separated)", key="run_kw",
        placeholder="python developer, automation engineer")

    row1 = st.columns(3)
    location = row1[0].text_input("Location", key="run_loc",
                                  placeholder="Metro Manila")
    pages = row1[1].number_input("Pages per keyword", 1, 20,
                                 value=int(config.DEFAULT_PAGES), key="run_pages")
    delay = row1[2].number_input("Delay between pages (s)", 0.0, 30.0,
                                 value=float(config.DEFAULT_DELAY_SECONDS),
                                 step=0.5, key="run_delay")
    sites = st.multiselect("Sites", _SITE_OPTIONS,
                           default=list(config.DEFAULT_SITES), key="run_sites")

    row2 = st.columns(3)
    max_years = row2[0].number_input("Your years of experience (0 = no filter)",
                                     0, 50, value=0, key="run_years")
    min_score = row2[1].number_input("Min score % (0 = no filter)", 0, 100,
                                     value=0, key="run_minscore")
    min_salary = row2[2].number_input("Min salary PHP (0 = no filter)", 0,
                                      1_000_000, value=0, step=5000,
                                      key="run_minsalary")

    row3 = st.columns(4)
    full_desc = row3[0].checkbox("Full descriptions", key="run_fulldesc",
                                 help="Visit each job's page (slower, richer).")
    only_new = row3[1].checkbox("Export only new", key="run_onlynew")
    rescore = row3[2].checkbox("Re-score stored", key="run_rescore")
    email = row3[3].checkbox("Email digest", key="run_email")

    skills_path = st.text_input("Skills file", value=config.DEFAULT_SKILLS_FILE,
                                key="run_skills")

    if st.button("Run search", type="primary", key="run_search_btn"):
        if not resume_pdf or not keyword:
            st.error("A resume PDF path and at least one keyword are required.")
            return
        if not sites:
            st.error("Select at least one site.")
            return
        args = [resume_pdf, keyword, "--site", ",".join(sites),
                "--pages", str(int(pages)), "--delay", str(delay)]
        if location:
            args += ["--location", location]
        if skills_path and skills_path != config.DEFAULT_SKILLS_FILE:
            args += ["--skills", skills_path]
        if max_years:
            args += ["--max-years", str(int(max_years))]
        if min_score:
            args += ["--min-score", str(int(min_score))]
        if min_salary:
            args += ["--min-salary", str(int(min_salary))]
        if full_desc:
            args.append("--full-desc")
        if only_new:
            args.append("--only-new")
        if rescore:
            args.append("--rescore")
        if email:
            args.append("--email")
        with st.spinner("Scraping and scoring — this can take a few minutes…"):
            _run_cli(args)


def _render_maintenance() -> None:
    """The one-shot CLI commands, as buttons — no terminal needed."""
    st.subheader("Maintenance & tools")
    pdf = st.session_state.get("run_pdf", "")

    row1 = st.columns(3)
    if row1[0].button("Backup database", key="run_backup",
                      width="stretch"):
        _run_cli(["--backup"])
    if row1[1].button("Calibrate score scale", key="run_calibrate",
                      width="stretch"):
        _run_cli(["--calibrate"])
    if row1[2].button("List stalled applications", key="run_stalled",
                      width="stretch"):
        _run_cli(["--stalled"])

    row2 = st.columns(3)
    if row2[0].button("Show AI usage", key="run_aiusage", width="stretch"):
        _run_cli(["--ai-usage"])
    if row2[1].button("List resumes", key="run_listresumes", width="stretch"):
        _run_cli(["--list-resumes"])
    prune_days = row2[2].number_input("Prune jobs older than (days)", 1, 365,
                                      value=30, key="run_prunedays")
    if row2[2].button("Prune old jobs", key="run_prune", width="stretch"):
        _run_cli(["--prune-days", str(int(prune_days))])

    st.caption("These operate on the current database. The two below read your "
               "resume PDF — set the path in the search form above first.")
    row3 = st.columns(2)
    if row3[0].button("Generate skills from PDF", key="run_genskills",
                      width="stretch"):
        if pdf:
            _run_cli([pdf, "--generate-skills"])
        else:
            st.error("Enter your resume PDF path in the search form first.")
    if row3[1].button("Import resume from PDF", key="run_importresume",
                      width="stretch"):
        if pdf:
            _run_cli([pdf, "--import-resume"])
        else:
            st.error("Enter your resume PDF path in the search form first.")


def _render_run_tab() -> None:
    """Search + maintenance — full parity with the main.py terminal commands."""
    _render_search_form()
    st.divider()
    _render_maintenance()
    st.caption("Live progress is also written to `logs/automation.log`.")


# ======================================================
# SETTINGS (provider, per-capability mode, cost meter)
# ======================================================
_CAPABILITY_LABELS = {
    "explain": "Score explanation",
    "summary": "Job summary",
    "cover_letter": "Cover letter",
    "interview": "Interview prep",
    "salary": "Salary read",
}


def _render_settings() -> None:
    """Provider status, per-capability default mode, and the cost meter."""
    provider = llm.get_provider(db_handler)
    usage = app_settings.usage_summary()

    st.subheader("AI provider")
    if provider.is_available():
        st.success(f"Provider ready: **{usage['provider']}** · model "
                   f"`{usage['model']}`")
    else:
        st.info("AI mode is off — no provider configured. Set one in `.env` "
                "(see `.env.example`); until then every feature runs in "
                "Standard mode regardless of the settings below.")

    st.subheader("Default mode per feature")
    st.caption("Standard is deterministic and free. Set a feature to AI to run "
               "its enrichment by default — it still falls back to Standard if "
               "no provider is configured or a call fails. You can always "
               "override per action.")
    for capability in app_settings.CAPABILITIES:
        current = app_settings.mode_for(capability)
        choice = st.radio(
            _CAPABILITY_LABELS.get(capability, capability),
            ["standard", "ai"],
            index=0 if current == "standard" else 1,
            horizontal=True, key=f"mode_{capability}")
        if choice != current:
            app_settings.set_mode(capability, choice)
            st.rerun()

    st.subheader("Cost meter")
    columns = st.columns(3)
    columns[0].metric("AI calls (cached)", f"{usage['calls']:,}")
    columns[1].metric("Input tokens", f"{usage['input_tokens']:,}")
    columns[2].metric("Output tokens", f"{usage['output_tokens']:,}")
    if usage["local"]:
        st.caption("Estimated cost: **$0.00** — a local model, nothing billed.")
    elif usage["cost_usd"] is not None:
        st.caption(f"Estimated cost: **${usage['cost_usd']:.2f}** at list price "
                   f"for `{usage['model']}` (cached calls cost nothing to "
                   "re-run).")
    else:
        st.caption(f"Estimated cost: unknown — no list price on file for "
                   f"`{usage['model']}`.")


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
        st.warning("No jobs in the database yet. Run a search below to "
                   "populate it — no terminal needed.")
        _render_run_tab()
        return

    filters = _render_sidebar(frame)
    _render_resume_picker()
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

    (matches_tab, detail_tab, board_tab, analytics_tab, run_tab,
     settings_tab) = st.tabs(
        ["Matches", "Job detail", "Board", "Analytics", "Run & tools",
         "Settings"])

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
        _render_pipeline()
        _render_analytics()

    with run_tab:
        _render_run_tab()

    with settings_tab:
        _render_settings()


run_dashboard()

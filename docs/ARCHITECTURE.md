# Architecture — AI Job Search Assistant

Design document for evolving `automation-job-finder` from a two-site scraper into a
local-first job search assistant.

**Organising principle: every feature works completely without AI.**
Standard mode is the product. AI mode is an enrichment layer that can be absent,
misconfigured, or switched off per feature without degrading anything.

| | |
|---|---|
| Target | Python 3.11+ (running 3.13 today) |
| Stack | Playwright · SQLite · Streamlit |
| Modes | Standard (free, offline, deterministic) + AI (optional) |
| Estimate | ~63 working days, ~12 weeks part-time |

---

## 1. Fix these first

Four defects in the current code that will distort the new features. None takes
more than a day.

### 1.1 The score scale is unreachable

`matcher._score_job()` normalises against a denominator no advertisement can reach:

```python
weighted     = 3*(title hits) + 1*(body hits)
max_weighted = 3 * len(resume_skills)     # every skill, in the title
score        = weighted / max_weighted * 100
```

With ~34 skills the denominator is 102, so the best of 315 stored jobs scores
**13.2%** and 309 cluster below 10%. Feature 10 ("explain why this scored 89%")
is unbuildable until this is fixed, and hundreds of ties mean the ranking barely
ranks.

```python
TARGET_MATCH = 8      # a strong ad hits ~8 of your skills — calibrate once
denominator  = 3 * min(len(resume_skills), TARGET_MATCH)
score        = min(100, weighted / denominator * 100)
```

Calibrate so the best jobs land at 80–95 and the median near 40. Store the scale
version in `meta` so old and new scores are never silently compared, then
`--rescore`.

### 1.2 skills.txt has drifted from the alias map

`SKILL_ALIASES` keys must match `skills.txt` lines exactly — `skill_in_text()`
does a plain dict lookup with no normalisation. Renaming `React JS` to `React.js`
silently detached that entry from its aliases. Phrases like
`REST API development`, `Python scripting`, and `Process automation` never appear
verbatim in an advertisement and contribute nothing. Feature 16 is the durable
fix; realign the keys by hand until then.

### 1.3 No backup before destructive operations

`output/jobs.db` holds months of history and application statuses that exist
nowhere else, and `init_db()` runs `ALTER TABLE` against it in place. Add a
timestamped copy before every migration, exposed as `--backup`.

### 1.4 Python version

You asked for 3.11; the environment is 3.13.9 and the code already uses `int | None`
unions requiring 3.10+. Target 3.11 as the **floor** in `pyproject.toml`, keep
running 3.13.

---

## 2. Dual-mode architecture

### 2.1 Why the interface splits in two

The proposed tree puts `RuleBasedProvider` as a sibling of the vendor adapters,
with every feature calling `AIProvider.analyze()`. That does not survive five
features returning five different shapes: gap analysis returns skill sets and
percentages, the cover letter writer returns a document, interview prep returns
question/answer pairs. A method general enough for all three returns `dict`, which
discards validation exactly where output is least trustworthy — and it forces
`RuleBasedProvider` to implement `summarise_job()` and `write_cover_letter()` on
one class with no shared state.

Split into **capability** protocols (one per feature, two implementations each)
and a **transport** protocol underneath that only the AI implementations touch:

```
application/use cases
        │
        ▼
capability protocols          GapAnalyzer · ResumeOptimizer · CoverLetterWriter
   (one per feature)          InterviewCoach · JobSummarizer
        │
        ├── Rule implementations ....... deterministic · offline · free
        │
        └── AI implementations ......... grounded in rule output
                    │
                    ▼
            LLMProvider (transport)
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
  OpenAICompatible  Claude   Gemini
  (OpenAI, Ollama,
   LM Studio, vLLM)
```

Your tree is otherwise right — `RuleBasedProvider` moves up one layer, the vendor
adapters stay where you put them. What this buys: each capability keeps a typed
signature, rule classes carry no dead methods, and mode is selectable **per
feature** (AI gap analysis while summaries stay deterministic).

```python
# ports/capabilities.py
class GapAnalyzer(Protocol):
    mode: Literal["standard", "ai"]
    def analyse(self, job: Job, resume: Resume) -> GapReport: ...

class ResumeOptimizer(Protocol):
    mode: Literal["standard", "ai"]
    def optimise(self, resume: Resume, job: Job) -> OptimisedResume: ...

class CoverLetterWriter(Protocol):
    mode: Literal["standard", "ai"]
    def write(self, resume: Resume, job: Job) -> CoverLetter: ...

class InterviewCoach(Protocol):
    mode: Literal["standard", "ai"]
    def prepare(self, job: Job, resume: Resume,
                difficulty: Difficulty) -> InterviewPack: ...

class JobSummarizer(Protocol):
    mode: Literal["standard", "ai"]
    def summarise(self, job: Job) -> JobSummary: ...
```

### 2.2 AI mode consumes Standard mode

The decision that makes dual mode pay for itself twice: the AI implementation
takes the rule-based result as **input** rather than re-deriving facts from raw text.

```python
class AIGapAnalyzer:
    mode = "ai"

    def __init__(self, baseline: GapAnalyzer, llm: LLMProvider) -> None:
        self._baseline, self._llm = baseline, llm   # baseline is the rule engine

    def analyse(self, job: Job, resume: Resume) -> GapReport:
        facts = self._baseline.analyse(job, resume)      # deterministic truth
        enriched = self._llm.complete(LLMRequest(
            system=GAP_SYSTEM,
            prompt=render(facts=facts, job=job.description),
            schema=GapNarrative,                         # explanation fields only
            effort="high",
        ))
        return facts.with_narrative(enriched.parsed)     # numbers stay authoritative
```

Three consequences:

- **Accuracy** — the model never invents a percentage; it is handed the computed
  one and asked to explain it.
- **Cost** — a compact fact block instead of full resume + full advertisement,
  roughly halving input tokens.
- **Verifiability** — every number in the UI came from arithmetic you can audit.

**Standard mode is also the lie detector.** Because the rule engine already knows
which skills are in the resume, an AI claim can be checked against it. If the
narrative says "consider adding Playwright" while the matcher found Playwright,
that contradiction is detectable in code — reject the narrative and fall back to
the deterministic report. Consumer AI tools cannot do this; they have no ground truth.

### 2.3 Six providers, three adapters

OpenAI, Ollama, LM Studio, vLLM, and LocalAI all speak the OpenAI chat-completions
wire format. One adapter parameterised by base URL and model covers all of them,
including both local options.

| Provider | Adapter | Base URL | Notes |
|---|---|---|---|
| OpenAI | `OpenAICompatible` | `api.openai.com/v1` | needs `OPENAI_API_KEY` |
| Ollama | `OpenAICompatible` | `localhost:11434/v1` | fully local, no key |
| LM Studio | `OpenAICompatible` | `localhost:1234/v1` | fully local, no key |
| vLLM / LocalAI | `OpenAICompatible` | configurable | same wire format |
| Claude | `ClaudeProvider` | — | official SDK, schema-validated output |
| Gemini | `GeminiProvider` | — | add only if actually wanted |
| *none configured* | `NullProvider` | — | every capability stays Standard |

### 2.4 Transport protocol

```python
# ports/llm.py
@dataclass(frozen=True)
class LLMRequest:
    system: str
    prompt: str
    schema: type | None = None      # pydantic model for structured output
    max_tokens: int = 4096
    effort: str = "medium"

@dataclass(frozen=True)
class LLMResponse:
    text: str
    parsed: object | None
    model: str
    input_tokens: int
    output_tokens: int
    from_cache: bool = False

class LLMProvider(Protocol):
    name: str
    def complete(self, request: LLMRequest) -> LLMResponse: ...
    def is_available(self) -> bool: ...
```

The Claude adapter, using the official SDK so structured output is validated
rather than parsed out of prose:

```python
# infrastructure/llm/claude.py
import anthropic

class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def is_available(self) -> bool:
        return bool(self._client.api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs = dict(
            model=self._model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=[{"role": "user", "content": request.prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": request.effort},
        )
        if request.schema is not None:
            response = self._client.messages.parse(**kwargs,
                                                   output_format=request.schema)
            parsed = response.parsed_output
        else:
            response = self._client.messages.create(**kwargs)
            parsed = None

        text = next((b.text for b in response.content if b.type == "text"), "")
        return LLMResponse(text=text, parsed=parsed, model=response.model,
                           input_tokens=response.usage.input_tokens,
                           output_tokens=response.usage.output_tokens)
```

### 2.5 Response cache

A job description does not change between runs, so analysing it twice is wasted
money. Wrap any provider — same protocol, SQLite-backed memo keyed by
`sha256(provider, model, system, prompt)`.

```python
class CachedProvider:
    def __init__(self, inner: LLMProvider, repo: AICacheRepository) -> None:
        self._inner, self._repo = inner, repo
        self.name = f"{inner.name}+cache"

    def is_available(self) -> bool:
        return self._inner.is_available()

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = sha256_of(self._inner.name, request)
        if (hit := self._repo.get(key)) is not None:
            return replace(hit, from_cache=True)
        result = self._inner.complete(request)
        self._repo.put(key, result)
        return result
```

This also makes development free — your tenth test run replays the first.

> **Prompt caching will not help here.** Anthropic's minimum cacheable prefix on
> Opus 4.8 is 4,096 tokens; a one-page resume plus system prompt is ~1,200. Below
> the minimum it silently does nothing — no error, just
> `cache_creation_input_tokens: 0`. The SQLite cache is where the saving is.

### 2.6 Mode selection rules

- **Default is Standard.** A fresh install with no `.env` never attempts a model call.
- **Mode is per capability** — `config.MODES = {"gap": "ai", "summary": "standard"}`,
  overridable per action in the UI.
- **Failure degrades, never errors.** `LLMUnavailable`, timeout, or schema-validation
  failure falls back to the rule result and shows one line saying enrichment was skipped.
- **Provenance is stored.** Every artefact records mode, provider, and model.

### 2.7 Model choice per task

| Task | Model | Effort | $/1M in | Why |
|---|---|---|---|---|
| Gap narrative, rewriting, cover letters | `claude-opus-4-8` | high | $5.00 | judgement and prose quality are the product |
| Summaries, extraction, red flags | `claude-haiku-4-5` | low | $1.00 | structured extraction; schema does the work |
| Bulk summaries (300+) | Haiku via Batch API | low | $0.50 | 50% off, not latency-sensitive |
| Fully-local mode | Ollama / LM Studio | — | $0.00 | nothing leaves the machine |

Expected spend in AI mode: **~$2–6/month** at ~40 new jobs/day, with summaries
batched and gap analysis run on demand for the jobs you actually care about.

---

## 3. The five features, both modes

### 3.1 Resume gap analysis

**Standard.** Set operations over canonical skills once §1.2 is done.
`matched = job ∩ resume`, `missing = job − resume`. Skill match is
`|matched| / |job skills|`; technology match is the same ratio restricted to
categories `language, framework, database, cloud, tool` — the number that
actually predicts a callback. ATS match is keyword coverage of the ad's most
frequent non-stopword terms, not just your skill list.

Priority ranking is computable from data you already collect — your own example
("learn Docker first, it appears in 72% of matching jobs") needs no model:

```sql
SELECT js.skill,
       COUNT(DISTINCT js.job_key) AS demand,
       ROUND(100.0 * COUNT(DISTINCT js.job_key) / (
           SELECT COUNT(*) FROM jobs
           WHERE archived = 0 AND search_keyword = :role
       ), 1) AS pct_of_role
FROM job_skills js
JOIN jobs j ON j.job_key = js.job_key
WHERE j.archived = 0
  AND j.search_keyword = :role
  AND js.skill NOT IN (SELECT skill FROM resume_skills WHERE resume_id = :rid)
GROUP BY js.skill
ORDER BY demand DESC
LIMIT 10;
```

*Caveat:* below ~150 jobs for a role that percentage is noise dressed as a
statistic. Suppress it and show a plain rank until the count crosses the threshold.

**AI.** Receives the computed fact block, returns narrative only — why the score
landed where it did, strengths, weaknesses, career advice, ordered improvement
plan. Never returns a number that reaches the UI.

### 3.2 Resume optimiser

> **Structural decision required first.** Standard mode is specified as "reorder
> sections, improve formatting, optimise keywords" — but the resume is currently a
> flat text blob from `pdfplumber`, and you cannot reliably reorder sections of a
> document whose structure was never captured. Adopt a **master resume**: one
> Markdown/YAML file as source of truth (contact block, summary, roles each with
> bullets and tags), rendered to DOCX and PDF. Import from PDF once to bootstrap,
> then never parse a PDF again. Both modes become straightforward and export stops
> being lossy.

**Standard.** Reorder sections so those carrying matched skills come first;
promote bullets containing job keywords within each role; surface
present-but-unused keywords the ad asks for; emit an ATS score. No wording
changes at all.

| ATS check | Weight | Rule |
|---|---|---|
| Text is extractable | 25 | selectable text, not an image scan |
| Keyword coverage | 25 | share of the job's top-20 terms present |
| Standard section headings | 15 | Experience / Education / Skills detected |
| Single-column layout | 15 | no tables or multi-column blocks |
| Parseable dates | 10 | `MMM YYYY` or `MM/YYYY`, consistent |
| Contact block complete | 5 | email + phone in the first 15 lines |
| Length | 5 | 1–2 pages under 10 years' experience |

**AI.** Rewrites existing bullets for impact and keyword alignment, operating on
the structured object so it can only touch text it was given.

**Anti-fabrication is enforced in code, not by the prompt:**

```python
def verify_no_fabrication(original: str, generated: str) -> list[str]:
    """Returns claims present in the draft but absent from the resume."""
    suspect = extract_entities(generated)   # orgs, dates, titles, numbers
    known   = extract_entities(original)
    return [claim for claim in suspect if claim not in known]
```

Anything new fails the check and the draft is rejected rather than shown.

### 3.3 Cover letter generator

**Standard.** Three templates — direct, warm, technical — with named slots for
company, position, top three matched skills, most relevant role, and a hiring
manager placeholder. Deterministic, instant, free. Honest limitation: a template
letter reads like one; its value is volume applications where a letter is required
but unlikely to be read closely.

**AI.** Same slots, but the body is written from the resume's real accomplishments
and the ad's language, matching register. Constrained to qualifications present in
the resume and verified the same way as §3.2.

### 3.4 Interview preparation

**Standard.** A curated `data/questions.yaml` keyed by skill plus a general
behavioural bank. Selection is deterministic: take the job's detected
technologies, pull their question sets, weight by centrality to the ad, and scale
count and depth by the required years — which `_extract_required_years()` already
parses.

```yaml
docker:
  technical:
    - q: "What is the difference between an image and a container?"
      level: easy
    - q: "How would you reduce the size of a production image?"
      level: medium
  coding:
    - task: "Write a Dockerfile for a Flask app with pinned dependencies."
      level: medium
```

**AI.** Questions specific to this ad and this resume, model answers grounded in
the candidate's real projects, difficulty tiers. The static bank remains the
fallback and the seed set the model extends rather than duplicates.

### 3.5 Job summary

**Standard.** Salary, work arrangement, and posting date already work. The rest is
section extraction, which succeeds because ads use predictable headings — regex
over `Responsibilities|Duties|What you.ll do`,
`Requirements|Qualifications|What we.re looking for`, `Benefits|Perks|What we offer`,
capturing the block beneath and splitting on bullet markers. Expect ~80% coverage
on JobStreet, lower on OnlineJobs.ph where posts are freeform. When a heading is
absent, leave the field empty rather than guessing.

Rule-based red flags catch the obvious cases: requests for payment, "unlimited
earning potential", crypto/forex recruitment, no company name plus a personal
email, and salary stated only as "competitive" on a 5+ year role.

**AI.** Prose summary, pros and cons, career-growth read, and subtler red flags
(vague scope, three jobs in one posting, churn signals). Runs through the Batch
API nightly at half price rather than synchronously per job.

---

## 4. Project structure

```
automation-job-finder/
├── main.py                     # CLI orchestrator — numbered steps only
├── dashboard.py                # Streamlit entry point
├── config.py                   # settings, selectors, weights, MODES, paths
├── .env                        # secrets (never committed)
│
├── domain/                     # pure — no I/O, no third-party imports
│   ├── models.py               # Job, Resume, Skill, Application, GapReport
│   ├── scoring.py              # weighted match, normalisation, explanation
│   ├── skills.py               # alias resolution, canonicalisation
│   └── stages.py               # application lifecycle + legal transitions
│
├── ports/                      # protocols the application depends on
│   ├── capabilities.py         # GapAnalyzer, ResumeOptimizer, ...
│   ├── job_source.py           # JobSource.fetch(...) -> list[JobListing]
│   ├── repository.py           # JobRepository, ApplicationRepository, ...
│   ├── llm.py                  # LLMProvider transport
│   └── documents.py            # DocumentWriter.write(...) -> Path
│
├── application/                # use cases — orchestration only
│   ├── scrape_jobs.py
│   ├── score_jobs.py
│   ├── analyse_gap.py
│   ├── generate_document.py
│   ├── track_application.py
│   └── analytics.py
│
├── infrastructure/
│   ├── scrapers/               # jobstreet.py, onlinejobs.py, common.py
│   ├── persistence/            # sqlite_repository.py, migrations.py
│   ├── capabilities/           # rule_gap.py, ai_gap.py, rule_letter.py, ...
│   ├── llm/                    # openai_compatible.py, claude.py, gemini.py,
│   │                           #   cache.py, null.py, factory.py
│   ├── documents/              # docx_writer.py, pdf_writer.py, md_writer.py
│   ├── notifications/          # email.py, desktop.py
│   └── resume/                 # master_resume.py, pdf_import.py
│
├── ui/
│   ├── pages/                  # 1_Matches.py, 2_Board.py, 3_Analytics.py …
│   └── components/             # gap_readout.py, score_bar.py, filters.py
│
├── data/                       # questions.yaml, templates/, resources.yaml
├── docs/                       # this file
└── utils.py                    # retry, generic helpers only
```

**Dependency rule:** `domain/` imports only the standard library. `application/`
imports `domain/` and protocols, never a concrete adapter. `infrastructure/` and
`ui/` import inward freely and are the only places touching Playwright, SQLite, an
LLM SDK, or the filesystem.

**Migrate incrementally with `git mv`** — `scraper_jobstreet.py` becomes
`infrastructure/scrapers/jobstreet.py`, the scoring half of `matcher.py` becomes
`domain/scoring.py`, its CSV/HTML half becomes an exporter. History is preserved
and each move is independently testable.

---

## 5. API design

### 5.1 Use cases

Every user-visible action maps to one service class with a single `execute()`.
Dependencies arrive through the constructor as protocols.

| Use case | Signature | Returns |
|---|---|---|
| `ScrapeJobs` | `execute(keywords, sites, pages, *, full_desc=False)` | `RunSummary` |
| `ScoreJobs` | `execute(resume_id, *, only_new=True)` | `list[ScoreResult]` |
| `ExplainScore` | `execute(job_key, resume_id)` | `ScoreExplanation` |
| `AnalyseGap` | `execute(job_key, resume_id, *, refresh=False)` | `GapReport` |
| `SummariseJob` | `execute(job_keys, *, batch=True)` | `dict[str, JobSummary]` |
| `GenerateDocument` | `execute(job_key, resume_id, kind, fmt)` | `Path` |
| `CompareResumes` | `execute(job_key, resume_ids)` | `list[ResumeRanking]` |
| `PrepareInterview` | `execute(job_key, resume_id, difficulty)` | `InterviewPack` |
| `TrackApplication` | `execute(job_key, stage, note=None)` | `Application` |
| `ProposeSkillMerges` | `execute(*, min_occurrences=3)` | `list[SkillProposal]` |
| `ComputeAnalytics` | `execute(window_days=90)` | `AnalyticsSnapshot` |

### 5.2 Repository protocols

Four narrow protocols rather than one wide one, so a use case declares only what
it touches. The SQLite implementations are the only code that writes SQL.

```python
class JobRepository(Protocol):
    def upsert(self, jobs: Sequence[Job]) -> int: ...
    def get(self, job_key: str) -> Job | None: ...
    def existing_keys(self, keys: Sequence[str]) -> set[str]: ...
    def search(self, filters: JobFilters) -> list[Job]: ...
    def archive_stale(self, days: int) -> int: ...

class AnalysisRepository(Protocol):
    def get(self, job_key: str, resume_id: int, kind: str) -> dict | None: ...
    def put(self, job_key: str, resume_id: int, kind: str,
            payload: dict, mode: str, model: str) -> None: ...

class ApplicationRepository(Protocol):
    def record(self, job_key: str, stage: Stage, note: str | None) -> None: ...
    def history(self, job_key: str) -> list[ApplicationEvent]: ...
    def by_stage(self, stage: Stage) -> list[Job]: ...
    def stalled(self, days: int) -> list[Job]: ...   # feeds ghosted detection

class AICacheRepository(Protocol):
    def get(self, cache_key: str) -> LLMResponse | None: ...
    def put(self, cache_key: str, response: LLMResponse) -> None: ...
    def spend(self, since: date) -> TokenSpend: ...  # powers the cost meter
```

### 5.3 Shared result schemas

Both modes return the same types — Standard fills the factual fields and leaves
narrative empty; AI fills both. The UI renders one shape either way.

```python
# domain/schemas.py
class SkillScore(BaseModel):
    skill: str
    confidence: int = Field(ge=0, le=100)   # drives the readout bars
    evidence: str                           # resume phrase that earned it

class GapReport(BaseModel):
    fit_percent: int = Field(ge=0, le=100)  # computed, never model-authored
    skill_match: int = Field(ge=0, le=100)
    tech_match: int = Field(ge=0, le=100)
    ats_match: int = Field(ge=0, le=100)
    strong: list[SkillScore]
    partial: list[SkillScore]
    missing: list[str]
    priorities: list[SkillDemand]           # corpus-ranked, deterministic
    narrative: GapNarrative | None = None   # AI mode only
    mode: Literal["standard", "ai"]

class GapNarrative(BaseModel):
    """The only part an LLM authors."""
    explanation: str = Field(max_length=800)
    strengths: list[str]
    weaknesses: list[str]
    career_advice: str
    improvements: list[str] = Field(max_length=5)

class JobSummary(BaseModel):
    responsibilities: list[str]
    requirements: list[str]
    nice_to_have: list[str]
    salary_text: str | None
    benefits: list[str]
    work_arrangement: Literal["Remote", "Hybrid", "On-site", "Unstated"]
    company_overview: str | None
    pros: list[str]
    cons: list[str]
    red_flags: list[str]
    mode: Literal["standard", "ai"]

class SkillProposal(BaseModel):
    canonical: str
    merge_from: list[str]        # ReactJS, React.js, React -> React
    category: Literal["language", "framework", "database",
                      "cloud", "ai", "tool", "practice"]
    occurrences: int
    rationale: str
```

> **Feature 16 proposes, never writes.** A skill-list change silently restates
> every score in the database, and a wrong merge is invisible until rankings
> degrade — exactly the failure currently caused by manual alias drift. Show
> proposals with occurrence counts, apply only what is approved, rescore in the
> same action.

### 5.4 Application state machine

Eleven stages, most transitions illegal. Encoding this in the domain keeps the
rule in one place whether the change arrives from the board, the detail page, or
the CLI.

```python
class Stage(StrEnum):
    SAVED = "saved"
    INTERESTED = "interested"
    APPLIED = "applied"
    PHONE_INTERVIEW = "phone_interview"
    TECHNICAL_INTERVIEW = "technical_interview"
    HR_INTERVIEW = "hr_interview"
    FINAL_INTERVIEW = "final_interview"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    WITHDRAWN = "withdrawn"

TERMINAL = {Stage.ACCEPTED, Stage.REJECTED, Stage.GHOSTED, Stage.WITHDRAWN}
EXITS    = {Stage.REJECTED, Stage.GHOSTED, Stage.WITHDRAWN}

FORWARD: dict[Stage, set[Stage]] = {
    Stage.SAVED:               {Stage.INTERESTED, Stage.APPLIED},
    Stage.INTERESTED:          {Stage.APPLIED},
    Stage.APPLIED:             {Stage.PHONE_INTERVIEW,
                                Stage.TECHNICAL_INTERVIEW, Stage.OFFER},
    Stage.PHONE_INTERVIEW:     {Stage.TECHNICAL_INTERVIEW, Stage.HR_INTERVIEW},
    Stage.TECHNICAL_INTERVIEW: {Stage.HR_INTERVIEW, Stage.FINAL_INTERVIEW},
    Stage.HR_INTERVIEW:        {Stage.FINAL_INTERVIEW, Stage.OFFER},
    Stage.FINAL_INTERVIEW:     {Stage.OFFER},
    Stage.OFFER:               {Stage.ACCEPTED},
}

def can_move(current: Stage, target: Stage) -> bool:
    if current in TERMINAL:
        return False
    return target in EXITS or target in FORWARD.get(current, set())
```

**Ghosted is inferred, not typed.** Nobody remembers to mark a silence. Anything
in `APPLIED` or an interview stage with no event for 21 days is surfaced on the
board as a one-click suggestion — which is what makes the response-rate metric
trustworthy rather than flattering.

---

## 6. Database schema (v2)

Additive only. Every new table is created alongside the existing `jobs` and
`meta`, so an existing database upgrades in place.

```sql
-- Existing table, extended
ALTER TABLE jobs ADD COLUMN stage             TEXT DEFAULT 'new';
ALTER TABLE jobs ADD COLUMN stage_changed_at  TEXT;
ALTER TABLE jobs ADD COLUMN notes             TEXT;
ALTER TABLE jobs ADD COLUMN duplicate_of      TEXT;   -- job_key of the twin
ALTER TABLE jobs ADD COLUMN score_scale       INTEGER DEFAULT 1;
ALTER TABLE jobs ADD COLUMN run_id            INTEGER;

CREATE TABLE IF NOT EXISTS resumes (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,      -- master markdown/yaml
    raw_text    TEXT NOT NULL,
    is_default  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_skills (
    resume_id INTEGER NOT NULL,
    skill     TEXT NOT NULL,
    evidence  TEXT,
    PRIMARY KEY (resume_id, skill)
);

CREATE TABLE IF NOT EXISTS job_skills (
    job_key   TEXT NOT NULL,
    skill     TEXT NOT NULL,        -- canonical name
    category  TEXT,                 -- language|framework|database|cloud|ai|tool
    in_title  INTEGER DEFAULT 0,
    PRIMARY KEY (job_key, skill),
    FOREIGN KEY (job_key) REFERENCES jobs(job_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills(skill);

CREATE TABLE IF NOT EXISTS job_analysis (
    job_key    TEXT NOT NULL,
    resume_id  INTEGER NOT NULL,
    kind       TEXT NOT NULL,       -- gap|summary|explanation|interview
    payload    TEXT NOT NULL,       -- JSON matching the pydantic schema
    mode       TEXT NOT NULL,       -- standard|ai
    model      TEXT,                -- NULL in standard mode
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_key, resume_id, kind)
);

CREATE TABLE IF NOT EXISTS application_events (
    id          INTEGER PRIMARY KEY,
    job_key     TEXT NOT NULL,
    stage       TEXT NOT NULL,
    note        TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (job_key) REFERENCES jobs(job_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_job ON application_events(job_key);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY,
    job_key    TEXT NOT NULL,
    resume_id  INTEGER,
    kind       TEXT NOT NULL,       -- resume|cover_letter|interview_pack
    fmt        TEXT NOT NULL,       -- docx|pdf|md
    path       TEXT NOT NULL,
    mode       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    sites       TEXT,
    keywords    TEXT,
    jobs_found  INTEGER DEFAULT 0,
    jobs_new    INTEGER DEFAULT 0,
    errors      TEXT
);

CREATE TABLE IF NOT EXISTS ai_cache (
    cache_key     TEXT PRIMARY KEY, -- sha256(provider, model, system, prompt)
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    response      TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    created_at    TEXT NOT NULL
);
```

Two deliberate choices. `jobs.stage` duplicates the latest
`application_events.stage` — the board and every list view read it directly, and
denormalising one column beats a correlated subquery on every render.
`job_analysis` is keyed by resume as well as job, because "how do I fit this role"
has a different answer per resume, which is what feature 11 compares.

---

## 7. Interface

Five pages, each answering one question.

| Page | Answers | Primary control |
|---|---|---|
| Matches | What is new and worth my attention? | filter rail, sortable table, row → detail |
| Job detail | Why this score, and what am I missing? | gap readout, generate buttons, stage control |
| Board | Where does every application stand? | stage columns, advance/reject |
| Analytics | What does the market want, how am I converting? | skill demand, funnel, salary spread |
| Settings | Which resume, which provider, what alerts? | resume manager, mode switches, cost meter |

**The gap readout** is the component the product is built around — fit percentage,
then one bar per skill coloured semantically (strong / partial / absent) so the
shape reads before any number does, then the recommendation. Identical in both
modes; AI mode adds the narrative paragraph beneath it.

```
Full Stack Developer (Python & React)                             82%
Acme Digital · Makati · Hybrid · PHP 70,000–90,000

Python      ████████████████████  100
React.js    ███████████████████░   95
PostgreSQL  █████████████████░░░   88
REST API    ████████████████░░░░   84
Playwright  ███████████░░░░░░░░░   55
Docker      ████░░░░░░░░░░░░░░░░   20
AWS         ███░░░░░░░░░░░░░░░░░   15

Missing: Docker, AWS  ·  Docker appears in 72% of Full Stack roles you track
```

**Interaction rules**

- Never block on the network. AI actions run behind `st.spinner` with a written
  estimate; cached results return instantly with a *cached* marker.
- Stage changes are one interaction — write on change, toast naming what moved.
- Empty states explain: "Standard mode — add `OPENAI_API_KEY` or start Ollama to
  enable AI explanations" beats a blank panel.
- Dark mode via `.streamlit/config.toml`, not injected CSS — Streamlit 1.58
  removed the DOM hooks custom CSS relied on.
- **Kanban caveat:** Streamlit has no native drag-and-drop, and a real draggable
  board needs a custom React component (build step, extra dependency). Use stage
  columns with a dropdown plus Advance/Reject buttons — two clicks instead of a
  drag, ~⅕ the work. The eleven-stage state machine is what matters, not the gesture.

---

## 8. Feature verdicts

Effort in working days, part-time.

| # | Feature | Verdict | Days | Phase |
|---|---|---|---|---|
| 1 | Resume gap analysis | Build (both modes) | 6 | 2 + 3 |
| 2 | Resume optimiser | Build (needs master resume) | 6 | 2 + 3 |
| 3 | Cover letter generator | Build (both modes) | 3 | 2 + 3 |
| 4 | Interview preparation | Build (both modes) | 4 | 2 + 4 |
| 5 | Job summary | Build (both modes) | 4 | 2 + 3 |
| 6 | Skill frequency analytics | Build — no AI needed | 3 | 2 |
| 7 | Learning recommendations | Adapt — curated map + narrative | 3 | 4 |
| 8 | Salary analytics | Adapt — own corpus, not "market" | 3 | 4 |
| 9 | Company intelligence | Mostly cut — data unobtainable | 2 | 4 |
| 10 | Job explanation | Build — blocked on §1.1 | 2 | 1 |
| 11 | Resume comparison | Build | 3 | 4 |
| 12 | Advanced tracker | Build — highest value/hour | 5 | 1 |
| 13 | Weekly analytics | Build | 3 | 4 |
| 14 | Smart notifications | Adapt — quiet rules mandatory | 2 | 3 |
| 15 | Portfolio matching | Build | 2 | 4 |
| 16 | AI skill extraction | Build early — propose only | 3 | 1 |
| 17 | Dashboard overhaul | Build incrementally | 6 | 1–4 |
| 18 | Module restructure | Build | 4 | 0 |
| 19 | Provider abstraction | Build as §2 | 3 | 3 |
| 20 | This document | Done | — | — |

### Where to push back

**9 — Company intelligence.** Ratings, employee count, and interview difficulty
are Glassdoor and LinkedIn data. Both prohibit automated collection and both run
bot protection heavier than Indeed's, which already forced detect-and-skip.
Building it means breaking terms you have so far respected, or shipping empty
columns. What survives is honestly sourced: industry inferred from ad text, plus
posting frequency, average advertised salary, and hiring cadence computed from your
own database — more predictive than a star rating for a company that posts often.

**8 — Salary analytics.** "Average market salary" needs a market dataset you do
not have. Compare against your own corpus median for the same role and label it as
such. Calling it market rate would be inventing a number.

**7 — Learning recommendations.** A model asked for course URLs produces plausible
ones that 404. Curate a small skill → resource map; let the model write only the
narrative around it.

---

## 9. Roadmap

### Phase 0 — Foundations · 6 days
No new features. Make the existing tool correct and give it a shape that can
absorb the rest.
- Fix score normalisation; add `score_scale` to `meta`; rescore the corpus
- Realign `SKILL_ALIASES` with skills.txt
- `--backup` and automatic pre-migration backup
- Move modules into `domain/ ports/ application/ infrastructure/` via `git mv`
- Add pytest, seeded with the existing offline suite

### Phase 1 — The tracker · 13 days
Works without an API key; turns the scraper into a job-search system.
- Eleven-stage lifecycle, `application_events`, notes and timestamps
- Board page with stage columns and advance/reject
- Deterministic score explanation
- Skill extraction into `job_skills`; canonical alias resolution
- Cross-site duplicate flagging; title exclusion keywords

### Phase 2 — Standard mode, complete · 16 days
All five headline features, deterministic and offline. **No API key exists yet.
At the end of this phase you have a shippable product.**
- Master resume model; PDF import once, then Markdown as source of truth
- Rule gap analysis: matched, missing, percentages, ATS coverage, corpus priorities
- ATS scoring rubric and resume reordering
- Template cover letters; DOCX / PDF / Markdown export
- Question bank and deterministic interview packs
- Section extraction and rule-based red flags
- Skill demand analytics page

### Phase 3 — AI mode · 14 days
Added behind the capability protocols. Every feature already works, so nothing
here can break the product.
- Capability protocols, `LLMProvider` transport, cache, null object
- OpenAI-compatible adapter (OpenAI + Ollama + LM Studio) and Claude adapter
- AI narratives grounded in rule output, with contradiction checking
- Bullet rewriting and the fabrication verifier
- Batch summaries, pros/cons, subtle red flags
- Cost meter and per-capability mode switches
- Notifications with quiet rules

### Phase 4 — Depth & polish · 14 days
- Multi-resume comparison ranking
- AI interview packs over the static bank
- Corpus-relative salary analytics; weekly funnel
- Learning roadmap; portfolio matching; reduced company intelligence
- Dashboard polish, empty states, packaging

**Total: 63 working days, ~12 weeks part-time.** Dual mode improves the
sequencing: **phases 0–2 are a complete, free, offline product** (~7 weeks, no API
key, nothing to cancel). Phase 3 is purely additive, so you can ship, use the tool
daily, and decide from experience whether the AI layer is worth building.

---

## 10. Libraries

| Package | Needed by | For | Why this one |
|---|---|---|---|
| `pytest` | Phase 0 | tests | replaces the hand-rolled assertion script |
| `python-docx` | Phase 2 | DOCX export | pure Python, no Word required |
| `fpdf2` | Phase 2 | PDF export | pure Python; WeasyPrint needs GTK on Windows, `docx2pdf` needs Word |
| `pydantic` | Phase 3 | response schemas | already a Streamlit transitive dep |
| `httpx` | Phase 3 | OpenAI-compatible adapter | one dep covers OpenAI, Ollama, LM Studio |
| `win11toast` | Phase 3 | desktop notifications | native toast, no tray daemon |
| `anthropic` | Phase 3, optional | Claude adapter | official SDK, schema-validated output |

Standard mode adds exactly three packages, none of which touch the network. Put
the AI packages in an optional extra (`pip install -e ".[ai]"`) so a user who
never enables AI never installs an LLM client.

**Deliberately not added:** Plotly/Altair (Streamlit's built-in charts cover every
chart here), LangChain (the protocol in §2 is forty lines you control), SQLAlchemy
(one SQLite file, hand-written SQL, already behind a protocol), a vector database
(semantic matching can start with numpy cosine similarity over a few hundred
embeddings in memory).

---

## 11. Scale & risks

| Dimension | Comfortable to | Past that |
|---|---|---|
| Jobs in SQLite | ~100,000 | index `score_percent`, `stage`, `last_seen`; archive beyond a year |
| Dashboard rows | ~5,000 | server-side pagination; stop loading the full table into pandas |
| Job sites | ~8 | already solved — one adapter per site behind `JobSource` |
| Scrape duration | ~20 min | run sites concurrently; they share no state |
| AI spend | ~$10/mo | cache is the lever; move summaries fully to Batch |

**Risks**

- **Selector rot.** Both scrapers depend on markup you do not control, and
  JobStreet has changed twice already. Add `--check-selectors` as a
  one-page-per-site health check so a break is a report, not a silent zero-result run.
- **Terms of service.** Personal, low-volume, rate-limited use is what keeps this
  defensible. The three-second delay and page caps are load-bearing.
- **Model drift.** Pin the model in config, record it in `job_analysis.model`, so
  when output quality shifts you can see whether the model changed underneath you.
- **Hallucinated specifics.** The fabrication verifier covers resumes. Apply the
  same instinct everywhere: never let a model invent a URL, salary figure, or
  company fact presented as sourced.
- **Single-file database.** Months of history and every application status live in
  one `.db`. The backup command in Phase 0 is the cheapest insurance here.

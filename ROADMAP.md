# Implementation Roadmap: Medium & High Effort Features

> Inspired by [santifer/career-ops](https://github.com/santifer/career-ops). Focuses on the features that transform resume-tailor from a single-run CLI into a persistent, intelligent job search pipeline.

---

## Table of Contents

1. [Offer Evaluation & Scoring System](#1-offer-evaluation--scoring-system) — Medium
2. [Interview Story Bank](#2-interview-story-bank) — Medium
3. [HTML Template + Playwright PDF Generation](#3-html-template--playwright-pdf-generation) — Medium
4. [Negotiation Scripts & Comp Research](#4-negotiation-scripts--comp-research) — Medium
5. [Portal Scanner](#5-portal-scanner) — High
6. [Batch Processing](#6-batch-processing) — High

---

## 1. Offer Evaluation & Scoring System

**Goal:** Before tailoring a resume, evaluate whether the job is even worth applying to. Score every offer on multiple dimensions and recommend go/no-go.

**Why this matters:** Right now resume-tailor jumps straight to rewriting. Users waste API calls and time tailoring for jobs that are a poor fit. An evaluation step acts as a quality filter — career-ops reports that out of 740+ evaluated offers, only ~100 got a tailored CV.

### 1.1 Data Model

Add to `models.py`:

```python
class OfferScore(BaseModel):
    """Multi-dimensional offer evaluation."""
    cv_match: float = Field(ge=1, le=5, description="Skills/experience alignment")
    growth_potential: float = Field(ge=1, le=5, description="Career growth opportunity")
    compensation: float = Field(ge=1, le=5, description="Comp vs market rate")
    culture_signals: float = Field(ge=1, le=5, description="Remote policy, values, team")
    role_clarity: float = Field(ge=1, le=5, description="Clear responsibilities, not a dumping ground")
    red_flags: float = Field(ge=0, le=2, default=0, description="Negative adjustment for blockers")
    global_score: float = Field(ge=1, le=5, description="Weighted average minus red flags")
    recommendation: str = Field(description="apply | consider | skip")
    reasoning: str = Field(description="1-2 sentence justification")


class OfferEvaluation(BaseModel):
    """Full A-F evaluation block output."""
    score: OfferScore
    role_summary: str            # Block A: TL;DR of the role
    cv_match_analysis: str       # Block B: requirement-by-requirement match table
    gaps: list[str]              # Block B: identified gaps with mitigation strategies
    level_strategy: str          # Block C: seniority positioning advice
    comp_research: str           # Block D: market salary data (requires web search)
    personalization_plan: str    # Block E: top 5 CV changes for this role
    interview_stories: list[dict]  # Block F: STAR+R stories mapped to JD requirements
```

Update `RunMeta` to include `offer_score: Optional[float] = None` and `recommendation: Optional[str] = None`.

### 1.2 Evaluation Engine

Create `resume_tailor/evaluator.py`:

```
evaluate_offer(jd_text, profile_context, client) -> OfferEvaluation
```

**Implementation details:**

- Single Claude call with structured output requesting all 6 blocks + scoring dimensions
- System prompt instructs Claude to score each dimension 1-5 with evidence
- Global score = weighted average: `cv_match * 0.35 + growth * 0.15 + comp * 0.20 + culture * 0.15 + clarity * 0.15 - red_flags`
- Recommendation thresholds: `>= 4.0 → apply`, `3.5-3.9 → consider`, `< 3.5 → skip`
- If score < 3.5, print a strong warning and ask user to confirm before proceeding to tailoring

**Profile context integration:** The evaluator must read not just the resume but also the user's target roles, salary range, and deal-breakers from `config.yaml` (see expanded config below).

### 1.3 CLI Integration

Add `evaluate` command to `cli.py`:

```
resume-tailor evaluate --job-url <url>     # Evaluate only, no tailoring
resume-tailor evaluate --job <text>        # Same with pasted JD
resume-tailor evaluate --job-file <path>   # Same with file
```

Modify the existing `run` command to execute evaluation first:
1. Ingest JD
2. Run evaluation → print score + recommendation
3. If score < 3.5: warn and ask user to confirm
4. If confirmed or score >= 3.5: proceed to existing 7-step pipeline
5. Save evaluation report alongside run outputs in `data/runs/<run_id>/evaluation.md`

### 1.4 Evaluation Report Format

Save as `data/runs/<run_id>/evaluation.md`:

```markdown
# Evaluation: {Company} — {Role}

**Date:** 2026-04-06
**Score:** 4.2/5
**Recommendation:** APPLY
**URL:** {source}

---

## A) Role Summary
{role_summary}

## B) CV Match
{cv_match_analysis}

### Gaps
{gaps with mitigation}

## C) Level & Strategy
{level_strategy}

## D) Compensation Research
{comp_research}

## E) Personalization Plan
{personalization_plan}

## F) Interview Prep (STAR+R)
{stories table}
```

### 1.5 Config Expansion

Expand `AppConfig` in `models.py` and `config.yaml` to support evaluation:

```python
class AppConfig(BaseModel):
    github_username: Optional[str] = None
    linkedin_username: Optional[str] = None
    base_resume_path: str = "data/base_resume.docx"
    github_token: Optional[str] = None
    # New fields for evaluation
    target_roles: list[str] = Field(default_factory=list)  # e.g. ["ML Engineer", "AI Platform Engineer"]
    salary_range: Optional[str] = None                      # e.g. "$150k-$200k"
    location: Optional[str] = None
    remote_preference: Optional[str] = None                 # "remote" | "hybrid" | "onsite" | "any"
    deal_breakers: list[str] = Field(default_factory=list)  # e.g. ["no-equity", "Java-only"]
    preferred_company_stage: Optional[str] = None           # "startup" | "growth" | "enterprise" | "any"
```

### 1.6 Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `OfferScore`, `OfferEvaluation`, expand `AppConfig`, update `RunMeta` |
| `evaluator.py` | **New file.** `evaluate_offer()` function |
| `cli.py` | Add `evaluate` command, modify `run` to call evaluator first |
| `claude_engine.py` | Add `_build_evaluation_prompt()` helper or integrate into evaluator |
| `output_manager.py` | Save evaluation report alongside run outputs |
| `config.py` | Handle new config fields (backwards-compatible defaults) |

---

## 2. Interview Story Bank

**Goal:** Persist STAR+R stories across runs so the user accumulates a reusable set of 5-10 master behavioral interview stories.

**Why this matters:** Each `run` currently generates fresh STAR answers and throws them away. Over multiple applications, the user should build a bank of polished stories that cover common behavioral themes (leadership, failure, conflict, technical challenge, etc.). Career-ops calls this the "story bank" — accumulated across evaluations, reusable for any interview.

### 2.1 Data Model

Add to `models.py`:

```python
class InterviewStory(BaseModel):
    """A STAR+Reflection story for behavioral interviews."""
    id: str                              # slug: "led-ml-migration-2024"
    title: str                           # "Led ML Pipeline Migration"
    theme: str                           # "leadership" | "failure" | "conflict" | "technical" | "impact" | "collaboration"
    situation: str
    task: str
    action: str
    result: str
    reflection: str                      # What was learned / what would be done differently
    source_companies: list[str]          # Which JD evaluations generated this story
    jd_requirements_matched: list[str]   # Which JD requirements this story addresses
    times_used: int = 0                  # How many times recommended across evaluations
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

### 2.2 Story Bank Manager

Create `resume_tailor/story_bank.py`:

```python
STORY_BANK_PATH = DATA_DIR / "story_bank.json"

def load_story_bank() -> list[InterviewStory]: ...
def save_story_bank(stories: list[InterviewStory]) -> None: ...

def add_stories(new_stories: list[InterviewStory]) -> tuple[int, int]:
    """Merge new stories into bank. Returns (added, updated).
    
    Dedup logic:
    - If a story with similar title+theme exists, merge source_companies and 
      update if the new version has better content (longer, more specific)
    - Use Claude to detect semantic duplicates (same story, different wording)
    """

def find_relevant_stories(jd_requirements: list[str], client: Anthropic) -> list[InterviewStory]:
    """Given JD requirements, return the most relevant stories from the bank.
    
    Uses Claude to match requirements to story themes and content.
    Returns stories ranked by relevance, with suggestions for adaptation.
    """

def export_story_bank_markdown() -> str:
    """Export the full story bank as a readable markdown file for interview prep."""
```

### 2.3 Pipeline Integration

Modify `claude_engine.py`:

1. In `answer_questions()` (step 6): After generating STAR answers, parse them into `InterviewStory` objects
2. After `run_pipeline()` completes: Call `add_stories()` to persist any new stories
3. In the evaluation step (feature #1): When generating Block F interview prep, first check `find_relevant_stories()` to surface existing stories, then generate new ones only for uncovered requirements

Modify `output_manager.py`:

- In `save_run()`: After saving answers, extract stories and merge into story bank
- Add `data/story_bank.json` to run metadata for cross-referencing

### 2.4 CLI Integration

Add `stories` command to `cli.py`:

```
resume-tailor stories                  # List all stories in bank (table view)
resume-tailor stories export           # Export as markdown for interview prep
resume-tailor stories match --job-url  # Show which stories match a specific JD
resume-tailor stories add              # Manually add a story (interactive)
```

### 2.5 Story Export Format

`data/story_bank.md` (generated by `export`):

```markdown
# Interview Story Bank

## Leadership

### Led ML Pipeline Migration
**Theme:** Leadership | **Used:** 3 times
**Relevant for:** "lead technical teams", "drive architecture decisions", "mentor engineers"

- **Situation:** ...
- **Task:** ...
- **Action:** ...
- **Result:** ...
- **Reflection:** ...

---

## Technical Challenge

### Built Real-time Fraud Detection System
...
```

### 2.6 Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `InterviewStory` |
| `story_bank.py` | **New file.** Load/save/merge/find/export functions |
| `claude_engine.py` | Modify `answer_questions()` to return parseable STAR stories; integrate story bank lookup |
| `output_manager.py` | Call `add_stories()` after saving run |
| `cli.py` | Add `stories` command group |

---

## 3. HTML Template + Playwright PDF Generation

**Goal:** Replace the LibreOffice DOCX-to-PDF path with direct HTML template rendering via Playwright. Produces designer-quality, ATS-optimized PDFs with custom fonts and consistent layout.

**Why this matters:** The current pipeline is: build DOCX → call LibreOffice headless → get PDF. This has issues:
- LibreOffice is a heavy dependency (~500MB) and not always available
- DOCX formatting is limited — can't do gradients, custom fonts, precise spacing
- The resulting PDFs look like Word documents, not professional resumes
- Playwright is already a dependency (`requirements.txt` line 3)

Career-ops uses an HTML template with Space Grotesk + DM Sans, rendered via Playwright's `page.pdf()`. The result is pixel-perfect.

### 3.1 HTML Template

Create `templates/cv-template.html`:

**Design spec (adapted from career-ops but made our own):**
- **Fonts:** Inter (headings, 600 weight) + Source Sans 3 (body, 400 weight) — both Google Fonts, ATS-safe
- **Header:** Name in 24px bold, thin accent line, contact row (email | phone | location | linkedin | github)
- **Layout:** Single column, no sidebars (ATS requirement)
- **Section order (6-second scan optimized):**
  1. Header (name + contact)
  2. Professional Summary (2-3 lines, keyword-dense)
  3. Core Competencies (6-8 keyword tags in a flex grid)
  4. Work Experience (reverse chronological)
  5. Projects (top 3-4 relevant)
  6. Education
  7. Skills (single line)
- **Colors:** Dark text (#1a1a1a), accent color configurable via config
- **Margins:** 0.5in all sides
- **Paper:** Auto-detect letter vs A4 based on company location

**Template uses `{{PLACEHOLDER}}` syntax** — simple string replacement, no template engine dependency.

Key placeholders:
```
{{NAME}}, {{CONTACT_LINE}}, {{SUMMARY}}, {{COMPETENCY_TAGS}},
{{EXPERIENCE_HTML}}, {{PROJECTS_HTML}}, {{EDUCATION_HTML}},
{{SKILLS_LINE}}, {{PAGE_FORMAT}}, {{ACCENT_COLOR}}
```

### 3.2 PDF Generator

Create `resume_tailor/pdf_generator.py`:

```python
def generate_pdf(
    tailored: TailoredResume,
    jd_keywords: list[str],
    output_path: Path,
    page_format: str = "letter",       # "letter" | "a4"
    accent_color: str = "#0ea5e9",     # configurable
) -> Path:
    """Render tailored resume as PDF via Playwright.
    
    Steps:
    1. Read templates/cv-template.html
    2. Build competency tags from top JD keywords
    3. Build experience HTML with reordered bullets
    4. Build projects HTML with tech stack badges
    5. Replace all {{PLACEHOLDERS}}
    6. Write to /tmp/cv-{company}-{date}.html
    7. Launch Playwright, load HTML, call page.pdf()
    8. Return output PDF path
    """
    
def _build_experience_html(experience: list[ResumeExperience]) -> str:
    """Convert experience list to HTML blocks."""
    
def _build_projects_html(projects: list[ResumeProject]) -> str:
    """Convert projects list to HTML blocks."""
    
def _build_competency_tags(keywords: list[str], max_tags: int = 8) -> str:
    """Build flex-grid of keyword competency tags."""
    
def _detect_page_format(jd_text: str) -> str:
    """Detect letter vs A4 based on company location in JD.
    US/Canada → letter, everywhere else → A4."""
```

### 3.3 Pipeline Integration

Modify `output_manager.py`:

- In `save_run()`: Replace the `write_docx()` + `export_pdf()` path with:
  ```python
  # Generate both formats
  docx_path = run_dir / "tailored_resume.docx"
  write_docx(original_docx, tailored, docx_path)  # Keep DOCX for compatibility
  
  pdf_path = run_dir / "tailored_resume.pdf"
  generate_pdf(tailored, job.keywords, pdf_path)   # New HTML-based PDF
  ```
- The HTML PDF becomes the primary output; DOCX is kept as a fallback

### 3.4 Config

Add to `AppConfig`:

```python
pdf_accent_color: str = "#0ea5e9"    # Sky blue default
pdf_font_heading: str = "Inter"
pdf_font_body: str = "Source Sans 3"
```

### 3.5 Keeping DOCX

**Do not remove DOCX support.** Some ATS systems specifically request DOCX uploads. The CLI should produce both:
- `tailored_resume.pdf` — the pretty HTML-rendered version (primary)
- `tailored_resume.docx` — the ATS-safe DOCX (secondary)

### 3.6 Files Changed

| File | Change |
|------|--------|
| `templates/cv-template.html` | **New file.** HTML template with CSS |
| `pdf_generator.py` | **New file.** Playwright-based PDF rendering |
| `output_manager.py` | Use `generate_pdf()` for PDF output instead of LibreOffice |
| `resume_processor.py` | Keep `export_pdf()` as fallback but prefer new path |
| `models.py` | Add PDF config fields to `AppConfig` |
| `pyproject.toml` | Can remove `pypandoc` dependency (no longer needed for PDF) |

---

## 4. Negotiation Scripts & Comp Research

**Goal:** After evaluating an offer, provide actionable salary negotiation frameworks, market compensation data, and ready-to-use scripts for common negotiation scenarios.

**Why this matters:** Compensation negotiation is where the most financial value is created in a job search. A single successful negotiation can be worth $10-30k/year. Career-ops includes salary research and negotiation scripts in every evaluation.

### 4.1 Data Model

Add to `models.py`:

```python
class CompResearch(BaseModel):
    """Market compensation data for a role."""
    role_title: str
    company: Optional[str] = None
    salary_range_low: Optional[int] = None
    salary_range_mid: Optional[int] = None
    salary_range_high: Optional[int] = None
    currency: str = "USD"
    equity_notes: Optional[str] = None
    sources: list[str] = Field(default_factory=list)  # URLs or source names
    location_adjustment: Optional[str] = None
    notes: Optional[str] = None


class NegotiationScript(BaseModel):
    """A ready-to-use negotiation script for a specific scenario."""
    scenario: str          # "initial_offer", "counter", "geographic_discount", "competing_offer", "equity_negotiation"
    context: str           # When to use this script
    script: str            # The actual words to say/write
    key_principles: list[str]  # Underlying negotiation principles being applied
```

### 4.2 Negotiation Engine

Create `resume_tailor/negotiation.py`:

```python
def research_compensation(
    role: str,
    company: Optional[str],
    location: Optional[str],
    client: Anthropic,
) -> CompResearch:
    """Use Claude + web search to gather market comp data.
    
    Sources to query:
    - Levels.fyi (primary for tech)
    - Glassdoor
    - LinkedIn Salary
    - Blind (for specific companies)
    
    Implementation:
    - Use Claude to synthesize a web search query
    - Parse results into structured CompResearch
    - Flag when data is uncertain or sparse
    """

def generate_negotiation_scripts(
    comp_research: CompResearch,
    user_salary_target: Optional[str],
    offer_details: Optional[str],
    client: Anthropic,
) -> list[NegotiationScript]:
    """Generate scenario-specific negotiation scripts.
    
    Always generates these 5 scenarios:
    1. Initial offer response (buy time, express enthusiasm, don't commit)
    2. Counter-offer (anchor high, justify with market data)
    3. Geographic discount pushback ("I price my work by value, not zip code")
    4. Competing offer leverage (without lying)
    5. Equity/benefits negotiation (when base is capped)
    
    Each script is personalized with:
    - Actual market data from comp_research
    - User's specific experience/proof points from profile
    - Company-specific framing
    """
```

### 4.3 Integration with Evaluation

When evaluation (feature #1) runs Block D (Comp & Demand):
1. Call `research_compensation()` to get market data
2. Include comp data in the evaluation report
3. If the user proceeds to `run` (tailoring), offer to generate negotiation scripts
4. Save negotiation scripts in `data/runs/<run_id>/negotiation.md`

### 4.4 CLI Integration

Add `negotiate` command:

```
resume-tailor negotiate --role "ML Engineer" --company "Anthropic" --location "SF"
resume-tailor negotiate --run-id <id>    # Generate scripts for a previous run
```

### 4.5 Output Format

`data/runs/<run_id>/negotiation.md`:

```markdown
# Negotiation Playbook: {Company} — {Role}

## Market Data

| Metric | Value | Source |
|--------|-------|--------|
| P25 | $160k | Levels.fyi |
| P50 | $185k | Levels.fyi |
| P75 | $210k | Levels.fyi + Glassdoor |
| Equity | ~0.05% over 4y | Blind reports |

## Script 1: Initial Offer Response

**When:** You receive the first verbal or written offer.

> "Thank you for the offer — I'm genuinely excited about this role and the team.
> I'd like to take a couple of days to review the full package. Could you send
> me the details in writing? I want to give this the consideration it deserves."

**Principle:** Never accept or counter on the spot. Written offers are harder to retract.

## Script 2: Counter-Offer
...
```

### 4.6 Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `CompResearch`, `NegotiationScript` |
| `negotiation.py` | **New file.** `research_compensation()`, `generate_negotiation_scripts()` |
| `evaluator.py` | Call comp research during Block D evaluation |
| `output_manager.py` | Save negotiation.md in run directory |
| `cli.py` | Add `negotiate` command |

---

## 5. Portal Scanner

**Goal:** Automatically discover new job openings by scanning configured company career pages and job boards. Build a pipeline inbox of URLs to evaluate.

**Why this matters:** This is the highest-leverage feature for turning resume-tailor from a reactive tool (user brings a URL) into a proactive job search system. Career-ops pre-configures 45+ companies and 19 search queries across Ashby, Greenhouse, Lever, and Wellfound.

### 5.1 Architecture

```
User runs: resume-tailor scan
         │
         ▼
    ┌─────────────────┐
    │  Read portals.yml │ → company list + search queries + title filters
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │  3-Level Scan    │
    │                  │
    │  L1: Playwright  │ → Direct career page navigation (most reliable)
    │  L2: API calls   │ → Greenhouse JSON API (structured data)
    │  L3: Web search  │ → Broad discovery across job boards
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │  Filter & Dedup  │
    │                  │
    │  Title filter    │ → positive/negative keyword match
    │  Dedup vs history│ → scan-history.tsv + pipeline.md
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │  Update Pipeline │
    │                  │
    │  Add to pipeline │ → data/pipeline.md (pending URLs)
    │  Update history  │ → data/scan-history.tsv
    │  Print summary   │
    └─────────────────┘
```

### 5.2 Configuration File

Create `templates/portals.example.yml` (user copies to `portals.yml`):

```yaml
# Title filters — applied to ALL results
title_filter:
  positive:
    - "machine learning"
    - "ML engineer"
    - "AI engineer"
    - "data scientist"
    - "platform engineer"
  negative:
    - "intern"
    - "junior"
    - "student"
    - "part-time"
    - "contract"
  seniority_boost:
    - "senior"
    - "staff"
    - "principal"
    - "lead"

# Companies to track directly
tracked_companies:
  - name: Anthropic
    careers_url: https://jobs.ashbyhq.com/anthropic
    platform: ashby
    enabled: true

  - name: OpenAI
    careers_url: https://jobs.ashbyhq.com/openai
    platform: ashby
    enabled: true

  - name: Mistral
    careers_url: https://jobs.lever.co/mistralai
    platform: lever
    enabled: true

  - name: Retool
    api: https://api.greenhouse.io/v1/boards/retool/jobs
    platform: greenhouse
    enabled: true

  # ... 40+ more companies

# Broad search queries across job boards
search_queries:
  - name: "Ashby — ML Engineer"
    query: 'site:jobs.ashbyhq.com "machine learning engineer"'
    enabled: true

  - name: "Greenhouse — AI Platform"
    query: 'site:boards.greenhouse.io "AI platform engineer"'
    enabled: true

  - name: "Lever — ML"
    query: 'site:jobs.lever.co "ML engineer" OR "machine learning"'
    enabled: true

  - name: "Wellfound — AI"
    query: 'site:wellfound.com "AI engineer" remote'
    enabled: true
```

### 5.3 Data Model

Add to `models.py`:

```python
class ScanResult(BaseModel):
    """A single job listing discovered during a scan."""
    title: str
    company: str
    url: str
    platform: Optional[str] = None   # ashby, greenhouse, lever, custom
    discovered_via: str               # "playwright", "api", "websearch"
    scan_date: str                    # YYYY-MM-DD
    status: str = "added"            # added, skipped_title, skipped_dup


class PortalConfig(BaseModel):
    """Parsed portals.yml configuration."""
    title_filter: dict = Field(default_factory=dict)
    tracked_companies: list[dict] = Field(default_factory=list)
    search_queries: list[dict] = Field(default_factory=list)
```

### 5.4 Scanner Module

Create `resume_tailor/scanner.py`:

```python
class PortalScanner:
    """Multi-level job portal scanner."""
    
    def __init__(self, config_path: Path, history_path: Path, pipeline_path: Path):
        self.config = self._load_config(config_path)
        self.history = self._load_history(history_path)
        self.pipeline_urls = self._load_pipeline(pipeline_path)
    
    def scan(self, levels: list[int] = [1, 2, 3]) -> ScanSummary:
        """Run the full scan pipeline.
        
        Level 1: Playwright direct navigation
        Level 2: Greenhouse/Ashby API calls  
        Level 3: Web search queries
        """
        all_results: list[ScanResult] = []
        
        if 1 in levels:
            all_results += self._scan_playwright()
        if 2 in levels:
            all_results += self._scan_apis()
        if 3 in levels:
            all_results += self._scan_websearch()
        
        # Filter and dedup
        filtered = self._apply_title_filter(all_results)
        new_results = self._dedup(filtered)
        
        # Persist
        self._update_pipeline(new_results)
        self._update_history(all_results)  # Log everything, including skipped
        
        return ScanSummary(
            total_found=len(all_results),
            filtered=len(filtered),
            new_added=len(new_results),
            results=new_results,
        )
    
    def _scan_playwright(self) -> list[ScanResult]:
        """Level 1: Navigate each company's careers page with Playwright.
        
        For each company with careers_url:
        1. Launch headless browser
        2. Navigate to careers_url
        3. Extract all job listing links (title + URL)
        4. Handle pagination if present
        5. Handle SPA rendering (wait for content to load)
        
        Platform-specific extraction:
        - Ashby: div.ashby-job-posting-brief-list a
        - Greenhouse: div.opening a
        - Lever: div.posting a.posting-title
        - Custom: generic link extraction + Claude parsing
        """
    
    def _scan_apis(self) -> list[ScanResult]:
        """Level 2: Hit structured APIs.
        
        Greenhouse API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
        Returns JSON array of {title, absolute_url, location, ...}
        
        Ashby API: POST https://jobs.ashbyhq.com/api/non-user-graphql
        With graphql query for job postings
        """
    
    def _scan_websearch(self) -> list[ScanResult]:
        """Level 3: Execute configured search queries.
        
        For each enabled query in search_queries:
        1. Execute web search
        2. Parse results: extract title, URL, company
        3. Pattern: (.+?)(?:\s*[@|—–-]\s*|\s+at\s+)(.+?)$
        """
    
    def _apply_title_filter(self, results: list[ScanResult]) -> list[ScanResult]:
        """Filter results by title keywords.
        
        - Must contain at least 1 positive keyword (case-insensitive)
        - Must contain 0 negative keywords
        - Seniority boost keywords increase priority but don't filter
        """
    
    def _dedup(self, results: list[ScanResult]) -> list[ScanResult]:
        """Deduplicate against:
        - scan-history.tsv (URL already seen)
        - pipeline.md (URL already queued)
        - data/runs/ (company+role already evaluated)
        """
```

### 5.5 Pipeline File

Create `data/pipeline.md` (the inbox):

```markdown
# Job Pipeline

## Pending
- [ ] https://jobs.ashbyhq.com/anthropic/abc123 | Anthropic | ML Engineer
- [ ] https://boards.greenhouse.io/retool/jobs/456 | Retool | AI Platform Engineer

## Processed
- [x] https://... | Company | Role (2026-04-06, score: 4.2)
```

### 5.6 Scan History

Create `data/scan-history.tsv`:

```
url	first_seen	source	title	company	status
https://...	2026-04-06	playwright:anthropic	ML Engineer	Anthropic	added
https://...	2026-04-06	api:retool	Junior Dev	Retool	skipped_title
```

### 5.7 CLI Integration

```
resume-tailor scan                          # Full scan (all levels)
resume-tailor scan --level 1               # Playwright only
resume-tailor scan --level 2               # API only
resume-tailor scan --company anthropic     # Single company
resume-tailor scan --dry-run               # Show what would be found without saving

resume-tailor pipeline                     # Show pending pipeline
resume-tailor pipeline process             # Evaluate all pending (sequential)
resume-tailor pipeline process --limit 5   # Evaluate next 5
```

### 5.8 Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `ScanResult`, `PortalConfig`, `ScanSummary` |
| `scanner.py` | **New file.** `PortalScanner` class with 3-level scanning |
| `cli.py` | Add `scan` and `pipeline` command groups |
| `templates/portals.example.yml` | **New file.** Example portal configuration |
| `config.py` | Add `load_portals()` function |

### 5.9 Important Considerations

- **Rate limiting:** Playwright scans should be sequential per company, with 2-3 second delays between navigations. API calls can be parallel.
- **Error handling:** If a careers_url 404s, log it and try web search fallback. Don't crash the whole scan.
- **Privacy:** The scanner only reads public career pages. Never log in to any service.
- **Web search dependency:** Level 3 requires a web search tool. Use `httpx` + Google search or integrate with a search API.

---

## 6. Batch Processing

**Goal:** Process multiple job offers in parallel — evaluate, score, generate PDFs, and update tracker for 10+ offers in a single run.

**Why this matters:** Once the scanner (feature #5) populates the pipeline with dozens of URLs, processing them one-by-one is tedious. Batch processing lets the user go from "scan found 20 new offers" to "here are the 5 worth applying to" in one command.

### 6.1 Architecture

```
resume-tailor batch [--from-pipeline] [--parallel 3] [--min-score 3.5]
         │
         ▼
    ┌─────────────────┐
    │  Collect URLs    │ → from pipeline.md or --urls flag
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │  Worker Pool     │ → ThreadPoolExecutor or ProcessPoolExecutor
    │                  │
    │  Worker 1: URL1  │ → ingest → evaluate → (if score >= threshold) tailor → PDF
    │  Worker 2: URL2  │ → same
    │  Worker N: URLN  │ → same
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │  Merge Results   │
    │                  │
    │  Update tracker  │ → data/runs/ + applications tracker
    │  Update pipeline │ → mark as processed
    │  Print summary   │ → ranked table of all results
    └─────────────────┘
```

### 6.2 Data Model

Add to `models.py`:

```python
class BatchJob(BaseModel):
    """A single job in a batch processing run."""
    id: int
    url: str
    company: Optional[str] = None
    role: Optional[str] = None
    status: str = "pending"          # pending, processing, completed, failed
    score: Optional[float] = None
    recommendation: Optional[str] = None
    error: Optional[str] = None
    run_id: Optional[str] = None     # Links to data/runs/<run_id>
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class BatchState(BaseModel):
    """Persistent state for a batch processing run."""
    batch_id: str
    created_at: datetime
    jobs: list[BatchJob] = Field(default_factory=list)
    parallel_workers: int = 3
    min_score_for_tailoring: float = 3.5
```

### 6.3 Batch Processor

Create `resume_tailor/batch_processor.py`:

```python
class BatchProcessor:
    """Process multiple job offers in parallel."""
    
    def __init__(
        self,
        urls: list[str],
        parallel: int = 3,
        min_score: float = 3.5,
        evaluate_only: bool = False,
    ):
        self.state = BatchState(
            batch_id=f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            created_at=datetime.now(timezone.utc),
            parallel_workers=parallel,
            min_score_for_tailoring=min_score,
        )
        self.evaluate_only = evaluate_only
        self._init_jobs(urls)
    
    def run(self) -> BatchState:
        """Execute the batch, respecting parallelism and resumability.
        
        Implementation:
        1. Load or create batch state
        2. Filter to pending/failed jobs
        3. Process in parallel using ThreadPoolExecutor
        4. Each worker calls the full pipeline independently:
           a. Ingest JD from URL
           b. Evaluate (score + recommendation)
           c. If score >= min_score and not evaluate_only: tailor + PDF
           d. Save run outputs
        5. Update batch state after each completion
        6. Print running progress table
        7. Final summary: ranked results, total scores, recommendations
        """
    
    def _process_single(self, job: BatchJob) -> BatchJob:
        """Process a single job offer (runs in worker thread).
        
        Critical: Each worker gets its own Anthropic client instance.
        Critical: Playwright calls must be serialized (not thread-safe).
        Use a lock for Playwright-based JD ingestion.
        """
    
    def resume_batch(self, batch_id: str) -> BatchState:
        """Resume a previously interrupted batch.
        
        Reads batch state from data/batches/{batch_id}/state.json.
        Skips completed jobs, retries failed ones.
        """

    def _print_progress(self):
        """Live-updating progress table using Rich."""

    def _print_summary(self):
        """Final summary: ranked table of all results."""
```

### 6.4 Batch State Persistence

Batch state is saved to `data/batches/{batch_id}/state.json` after every job completion. This enables:
- **Resumability:** If the process crashes, re-run with `--resume <batch_id>` to continue
- **Progress tracking:** Other tools can read state.json to show progress
- **Audit trail:** Historical record of all batch runs

### 6.5 CLI Integration

```
resume-tailor batch --urls url1 url2 url3       # Process specific URLs
resume-tailor batch --from-pipeline              # Process all pending pipeline URLs
resume-tailor batch --from-pipeline --limit 10   # Process next 10 from pipeline
resume-tailor batch --parallel 5                 # Use 5 workers (default: 3)
resume-tailor batch --min-score 4.0              # Only tailor for 4.0+ (default: 3.5)
resume-tailor batch --evaluate-only              # Score only, no tailoring
resume-tailor batch --resume <batch_id>          # Resume interrupted batch
resume-tailor batch --dry-run                    # Show what would be processed
resume-tailor batch history                      # List past batch runs
```

### 6.6 Progress Output

While running:

```
Batch: batch_20260406_143022 | Workers: 3 | Min score: 3.5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 #  Company      Role                Score  Status      Time
 1  Anthropic    ML Engineer         4.5/5  APPLY ✓     12s
 2  Retool       AI Platform Eng     3.8/5  CONSIDER    15s
 3  OpenAI       Research Eng        ▶ Processing...    8s
 4  Mistral      ML Ops              Pending
 5  ElevenLabs   Voice AI Eng        Pending

Progress: 2/5 complete | 1 in progress | 2 pending
```

Final summary:

```
Batch Complete — 5 offers processed in 2m 14s
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Recommended (APPLY):
  4.5/5  Anthropic — ML Engineer          → report + PDF generated
  4.2/5  ElevenLabs — Voice AI Engineer   → report + PDF generated

Worth considering:
  3.8/5  Retool — AI Platform Engineer    → report generated (no PDF, below 4.0)

Skip:
  3.2/5  OpenAI — Research Engineer       → evaluation only
  2.8/5  Mistral — ML Ops                 → evaluation only

→ Run `resume-tailor review` to review the tailored resumes.
```

### 6.7 Thread Safety Considerations

- **Anthropic client:** Thread-safe — each worker can share the same client instance, but creating separate instances is cleaner for retry tracking.
- **Playwright:** NOT thread-safe. All browser-based JD ingestion must go through a shared lock or a dedicated browser worker thread. Alternative: pre-fetch all JDs sequentially, then process evaluations in parallel.
- **File writes:** Each worker writes to its own `data/runs/<run_id>/` directory. No conflicts. The batch state file is updated with a lock.
- **Console output:** Use Rich's `Live` display for thread-safe progress updates.

### 6.8 Recommended Implementation Strategy

Given thread-safety constraints, the cleanest architecture is a two-phase approach:

**Phase 1 (sequential):** Ingest all JDs
```python
for url in urls:
    jd_text = ingest_job_description(url=url)  # May use Playwright
    batch_jobs[url].jd_text = jd_text
```

**Phase 2 (parallel):** Evaluate + tailor (CPU/API-bound, no browser needed)
```python
with ThreadPoolExecutor(max_workers=parallel) as pool:
    futures = {pool.submit(process_job, job): job for job in batch_jobs}
```

### 6.9 Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `BatchJob`, `BatchState` |
| `batch_processor.py` | **New file.** `BatchProcessor` class |
| `cli.py` | Add `batch` command group |
| `output_manager.py` | Minor: support batch context in run metadata |

---

## Implementation Order

```
Phase 1 — Foundation (Week 1-2)
├── Feature 1: Offer Evaluation & Scoring   ← enables everything else
│   ├── Expand AppConfig
│   ├── Build evaluator.py
│   ├── Add CLI commands
│   └── Integrate into run pipeline
│
└── Feature 3: HTML PDF Generation          ← improves core output quality
    ├── Create HTML template
    ├── Build pdf_generator.py
    └── Wire into output_manager.py

Phase 2 — Intelligence (Week 3)
├── Feature 2: Interview Story Bank         ← compounds value over time
│   ├── Build story_bank.py
│   ├── Integrate with answer_questions()
│   └── Add CLI commands
│
└── Feature 4: Negotiation Scripts          ← builds on evaluation data
    ├── Build negotiation.py
    ├── Integrate with evaluator
    └── Add CLI command

Phase 3 — Scale (Week 4-5)
├── Feature 5: Portal Scanner               ← highest complexity
│   ├── Create portals.yml template
│   ├── Build scanner.py (L1: Playwright)
│   ├── Add API scanning (L2)
│   ├── Add web search scanning (L3)
│   ├── Pipeline management commands
│   └── Add CLI commands
│
└── Feature 6: Batch Processing             ← depends on scanner + evaluator
    ├── Build batch_processor.py
    ├── Two-phase architecture
    ├── Resumability + state persistence
    └── Add CLI commands
```

## New File Summary

| New File | Feature | Purpose |
|----------|---------|---------|
| `resume_tailor/evaluator.py` | 1 | Multi-dimensional offer scoring |
| `resume_tailor/story_bank.py` | 2 | Persistent STAR+R interview stories |
| `resume_tailor/pdf_generator.py` | 3 | Playwright HTML-to-PDF rendering |
| `templates/cv-template.html` | 3 | ATS-optimized resume HTML template |
| `resume_tailor/negotiation.py` | 4 | Comp research + negotiation scripts |
| `resume_tailor/scanner.py` | 5 | Multi-level portal scanning |
| `templates/portals.example.yml` | 5 | Scanner configuration template |
| `resume_tailor/batch_processor.py` | 6 | Parallel batch offer processing |

## Modified File Summary

| Existing File | Features Touching It |
|---------------|---------------------|
| `models.py` | 1, 2, 3, 4, 5, 6 |
| `cli.py` | 1, 2, 4, 5, 6 |
| `claude_engine.py` | 1, 2 |
| `output_manager.py` | 1, 3, 6 |
| `config.py` | 1, 5 |
| `pyproject.toml` | 3 |

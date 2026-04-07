# hunt

AI-powered job search pipeline. Evaluates offers, tailors resumes, scans portals, and generates negotiation scripts — all from the terminal.

---

## Features

| Feature | Status |
|---------|--------|
| Offer evaluation — A-F scoring across 5 dimensions | ✅ |
| 7-step resume tailoring (gap analysis → ATS-optimized output) | ✅ |
| Application question answering (STAR format) | ✅ |
| PDF generation via HTML template + Playwright | ✅ |
| GitHub profile enrichment | ✅ |
| LinkedIn profile scraping (4-strategy fallback) | ✅ |
| Interview story bank (persistent STAR+R stories) | ✅ |
| Salary comp research + negotiation scripts | ✅ |
| Portal scanner (Playwright + API + web search) | ✅ |
| Batch processing with parallel workers | ✅ |
| Review / approve workflow | ✅ |

---

## Installation

**Requirements:** Python 3.11+, an [Anthropic API key](https://console.anthropic.com/)

```bash
git clone https://github.com/<your-username>/hunt.git
cd hunt
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
```

Copy the example env file and add your API key:

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY
```

---

## Setup

Point hunt at your resume and optionally your GitHub/LinkedIn:

```bash
hunt setup \
  --resume path/to/your/resume.pdf \
  --github-username yourhandle \
  --linkedin-username yourhandle \
  --target-roles "ML Engineer, AI Platform Engineer" \
  --salary-range "$150k-$200k" \
  --remote remote
```

Accepts PDF and DOCX. PDFs are parsed by Claude and converted to a structured base.

---

## Usage

### Evaluate an offer before tailoring

```bash
hunt evaluate --job-url https://jobs.ashbyhq.com/company/job-id --company Acme --role "ML Engineer"
```

Scores the offer across 5 dimensions (CV match, growth, comp, culture, clarity) and gives a go/no-go recommendation before you spend time tailoring.

### Tailor a resume

```bash
# From a URL
hunt run --job-url https://jobs.ashbyhq.com/company/job-id --company Acme --role "ML Engineer"

# From pasted text
hunt run --job "We are looking for..." --company Acme --role "ML Engineer"

# From a file
hunt run --job-file job.txt

# With application questions
hunt run --job-url <url> -q "Why do you want to work here?" -q "Describe a technical challenge"
```

Runs offer evaluation first. If the score is below 3.5, asks before proceeding. Outputs land in `data/runs/<date-company-role>/`:

```
evaluation.md        ← A-F offer score + gap analysis
tailored_resume.pdf  ← ATS-optimized PDF (HTML → Playwright)
tailored_resume.docx ← DOCX fallback
gap_analysis.md      ← What changed and why
answers.md           ← STAR answers to application questions
negotiation.md       ← Comp research + scripts (if requested)
```

### Scan job portals

```bash
hunt scan              # Full scan: Playwright + API + web search
hunt scan --level 1    # Playwright only (most reliable)
hunt scan --level 2    # Greenhouse API only
hunt pipeline          # View pending offers from last scan
```

Reads from `portals.yml` (copy from `templates/portals.example.yml`). Discovers new openings from 10+ pre-configured companies and web search queries across Ashby, Greenhouse, Lever, and Wellfound.

### Batch process the pipeline

```bash
hunt batch --from-pipeline          # Process all pending offers
hunt batch --from-pipeline --limit 10  # Process next 10
hunt batch --urls url1 url2 url3    # Process specific URLs
hunt batch --evaluate-only          # Score only, no tailoring
hunt batch --parallel 5             # Use 5 workers (default: 3)
hunt batch-history                  # View past batch runs
```

### Negotiation playbook

```bash
hunt negotiate --role "ML Engineer" --company Anthropic --location "San Francisco"
hunt negotiate --run-id <id>   # Generate scripts for a previous run
```

Researches market comp (P25/P50/P75) and generates 5 ready-to-use scripts: initial offer response, counter-offer, geographic discount pushback, competing offer leverage, equity negotiation.

### Interview story bank

```bash
hunt stories list            # All stored STAR+R stories
hunt stories export          # Export to data/story_bank.md
hunt stories match --job-url <url>  # Which stories match this JD
```

Stories accumulate automatically across every `run`. Over time you build a reusable bank of 5–10 master stories that answer any behavioral question.

### Review and approve

```bash
hunt review          # Review latest run (opens PDF, shows gap report)
hunt review <run-id> # Review specific run
```

Approve to promote the tailored resume as your new base — future runs build on top of it.

### Other

```bash
hunt history         # All past runs
hunt config show     # Current configuration
```

---

## How It Works

```
hunt scan                         hunt run / hunt batch
     │                                     │
     ▼                                     ▼
┌─────────────┐               ┌─────────────────────┐
│ Portal Scan │               │    JD Ingestion      │
│             │               │ httpx → BS4 →        │
│ L1 Playwright              │ Playwright fallback   │
│ L2 API      │               └──────────┬──────────┘
│ L3 Search   │                          │
└──────┬──────┘               ┌──────────▼──────────┐
       │                      │  Offer Evaluation    │
       ▼                      │  5-dimension score   │
  pipeline.md                 │  go / no-go          │
  (URL inbox)                 └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │   Profile Build      │
                              │ Resume + GitHub      │
                              │ + LinkedIn           │
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │  Gap Analysis +      │
                              │  Resume Rewrite      │
                              │  ATS Check           │
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │  PDF + Story Bank    │
                              │  + Negotiation       │
                              └─────────────────────┘
```

---

## Project Structure

```
resume_tailor/
├── cli.py              # Entry point — all commands
├── models.py           # Pydantic data models
├── config.py           # Config loading (config.yaml + .env)
├── claude_engine.py    # 7-step tailoring pipeline
├── evaluator.py        # Offer scoring (A-F, 5 dimensions)
├── job_analyzer.py     # JD ingestion (URL / text / file)
├── resume_processor.py # DOCX read/write, PDF ingestion, ATS checks
├── pdf_generator.py    # HTML → PDF via Playwright
├── github_client.py    # GitHub API enrichment
├── linkedin_client.py  # LinkedIn scraping (4-strategy fallback)
├── story_bank.py       # Persistent STAR+R interview stories
├── negotiation.py      # Comp research + negotiation scripts
├── scanner.py          # 3-level portal scanner
├── batch_processor.py  # Parallel batch processing
└── output_manager.py   # Run persistence, review/approve flow

templates/
├── cv-template.html        # ATS-optimized HTML resume template
└── portals.example.yml     # Scanner config — copy to portals.yml

data/                   # Local data (gitignored — personal)
ROADMAP.md              # Detailed implementation plans
```

---

## Configuration

`config.yaml` is created by `hunt setup` and gitignored:

```yaml
github_username: yourhandle
linkedin_username: yourhandle
base_resume_path: data/base_resume.docx
target_roles:
  - "ML Engineer"
  - "AI Platform Engineer"
salary_range: "$150k-$200k"
remote_preference: remote
deal_breakers:
  - no-equity
pdf_accent_color: "#0ea5e9"
```

Secrets go in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
```

Portal scanner config lives in `portals.yml` (gitignored):

```bash
cp templates/portals.example.yml portals.yml
# Edit to add your target companies and role keywords
```

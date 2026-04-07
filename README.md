# resume-tailor

AI-powered job application pipeline. Evaluates offers, tailors resumes, answers application questions, and scans job portals — all from the terminal.

---

## Features

| Feature | Status |
|---------|--------|
| Offer evaluation with A-F scoring across 5 dimensions | Planned |
| 7-step resume tailoring (gap analysis → ATS-optimized output) | ✅ |
| Application question answering (STAR format) | ✅ |
| PDF generation via HTML template + Playwright | Planned |
| GitHub profile enrichment | ✅ |
| LinkedIn profile scraping (4-strategy fallback) | ✅ |
| Interview story bank (persistent STAR+R stories) | Planned |
| Salary comp research + negotiation scripts | Planned |
| Portal scanner (Playwright + API + web search) | Planned |
| Batch processing with parallel workers | Planned |
| Review / approve workflow | ✅ |

---

## Installation

**Requirements:** Python 3.11+, an [Anthropic API key](https://console.anthropic.com/)

```bash
git clone https://github.com/<your-username>/resume-tailor.git
cd resume-tailor
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

Point the tool at your resume and optionally your GitHub/LinkedIn:

```bash
resume-tailor setup \
  --resume path/to/your/resume.pdf \
  --github-username yourhandle \
  --linkedin-username yourhandle
```

Accepts both PDF and DOCX. PDFs are parsed by Claude and converted to a structured DOCX base.

---

## Usage

### Tailor a resume for a job

```bash
# From a job URL
resume-tailor run --job-url https://jobs.ashbyhq.com/company/job-id --company Acme --role "ML Engineer"

# From pasted job description
resume-tailor run --job "We are looking for..." --company Acme --role "ML Engineer"

# From a file
resume-tailor run --job-file job.txt

# With application questions
resume-tailor run --job-url <url> -q "Why do you want to work here?" -q "Describe a technical challenge"
```

The pipeline runs 7 steps:

1. Analyze job description (required skills, action verbs, tech stack)
2. Assemble candidate context (resume + GitHub + LinkedIn)
3. Gap analysis and tailoring strategy
4. Rewrite resume (reorder bullets, inject keywords, update summary)
5. ATS compliance check
6. Answer application questions in STAR format
7. Generate gap analysis report

Outputs land in `data/runs/<date-company-role>/`:

```
tailored_resume.docx
tailored_resume.pdf
gap_analysis.md
answers.md
run_meta.json
```

### Review and approve

```bash
resume-tailor review          # Review latest run (opens PDF, shows gap report)
resume-tailor review <run-id> # Review specific run
```

Approve to promote the tailored resume as your new base — future runs build on top of it.

### View history

```bash
resume-tailor history
```

### Configuration

```bash
resume-tailor config show
```

---

## How It Works

```
Job URL / text / file
        │
        ▼
┌──────────────────┐
│  JD Ingestion    │  httpx → BeautifulSoup → Playwright fallback
└────────┬─────────┘
         │
┌────────▼─────────┐
│  Profile Build   │  Resume + GitHub API + LinkedIn (4-strategy scrape)
└────────┬─────────┘
         │
┌────────▼─────────┐
│  Gap Analysis    │  Claude reasons about match, surfaces weaknesses
└────────┬─────────┘
         │
┌────────▼─────────┐
│  Resume Rewrite  │  Bullets reordered, keywords injected, summary updated
└────────┬─────────┘
         │
┌────────▼─────────┐
│  ATS Check       │  Keyword density, acronym coverage, red flags
└────────┬─────────┘
         │
┌────────▼─────────┐
│  Q&A + Report    │  STAR answers + gap analysis markdown
└────────┬─────────┘
         │
        DOCX + PDF + gap_analysis.md + answers.md
```

---

## Project Structure

```
resume_tailor/
├── cli.py              # Typer CLI entry point
├── config.py           # Config loading (config.yaml + .env)
├── models.py           # Pydantic data models
├── claude_engine.py    # All Claude API calls (7-step pipeline)
├── job_analyzer.py     # JD ingestion (URL / text / file)
├── resume_processor.py # DOCX read/write, PDF ingestion, ATS checks
├── github_client.py    # GitHub API profile enrichment
├── linkedin_client.py  # LinkedIn scraping (4-strategy fallback)
└── output_manager.py   # Run persistence, review/approve flow

templates/              # HTML resume template (PDF generation)
data/                   # Local data (gitignored — personal)
ROADMAP.md              # Detailed implementation plans for upcoming features
```

---

## Configuration Reference

`config.yaml` is created by `setup` and gitignored. Edit it directly or use `setup` flags:

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
```

Secrets go in `.env`, never in config.yaml:

```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
```

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for detailed implementation plans. Upcoming:

- **Offer scoring** — evaluate fit before spending tokens tailoring
- **HTML PDF** — designer-quality output via Playwright (no LibreOffice)
- **Story bank** — persistent STAR+R interview stories across all runs
- **Negotiation scripts** — market comp research + ready-to-use scripts
- **Portal scanner** — auto-discover jobs from 45+ company career pages
- **Batch processing** — evaluate 10+ offers in parallel

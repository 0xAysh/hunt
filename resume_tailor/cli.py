"""Typer CLI entry point for resume-tailor."""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Optional

import typer
from anthropic import Anthropic
from rich.console import Console

from .config import ensure_dirs, get_anthropic_api_key, load_config, save_config
from .models import AppConfig

app = typer.Typer(
    name="resume-tailor",
    help="AI-powered resume tailoring for job applications.",
    add_completion=False,
)
console = Console()

DATA_DIR = Path(__file__).parent.parent / "data"


# ── setup ──────────────────────────────────────────────────────────────────────

@app.command()
def setup(
    github_username: Annotated[Optional[str], typer.Option("--github-username", "-g")] = None,
    linkedin_username: Annotated[Optional[str], typer.Option("--linkedin-username", "-l")] = None,
    resume: Annotated[Optional[Path], typer.Option("--resume", "-r", help="Base DOCX resume path")] = None,
    github_token: Annotated[Optional[str], typer.Option("--github-token", help="GitHub PAT (stored in env, not config)")] = None,
) -> None:
    """Configure resume-tailor with your GitHub, LinkedIn, and base resume."""
    ensure_dirs()
    cfg = load_config()

    if github_username:
        cfg.github_username = github_username
    if linkedin_username:
        cfg.linkedin_username = linkedin_username
    if github_token:
        cfg.github_token = github_token

    if resume:
        src = Path(resume)
        if not src.exists():
            console.print(f"[red]Resume file not found: {src}[/red]")
            raise typer.Exit(1)
        dest = DATA_DIR / "base_resume.docx"
        marker = DATA_DIR / ".from_pdf"

        if src.suffix.lower() == ".pdf":
            console.print("[blue]PDF detected — extracting and converting to DOCX...[/blue]")
            api_key = get_anthropic_api_key()
            from anthropic import Anthropic as _Anthropic
            from .resume_processor import read_pdf, build_docx_from_resume
            resume_doc = read_pdf(src, _Anthropic(api_key=api_key))
            build_docx_from_resume(resume_doc, dest)
            marker.touch()  # flag that base was built from PDF
            console.print(f"[green]PDF converted and saved as {dest}[/green]")
        else:
            shutil.copy2(str(src), str(dest))
            marker.unlink(missing_ok=True)
            console.print(f"[green]Base resume copied to {dest}[/green]")

        cfg.base_resume_path = str(dest)

    save_config(cfg)
    console.print("[green]Configuration saved.[/green]")
    _show_config(cfg)


# ── run ────────────────────────────────────────────────────────────────────────

@app.command()
def run(
    job: Annotated[Optional[str], typer.Option("--job", "-j", help="Job description as text")] = None,
    job_url: Annotated[Optional[str], typer.Option("--job-url", "-u", help="URL to job posting")] = None,
    job_file: Annotated[Optional[Path], typer.Option("--job-file", "-f", help="Path to job description file")] = None,
    question: Annotated[Optional[list[str]], typer.Option("--question", "-q", help="Application question (repeatable)")] = None,
    questions_file: Annotated[Optional[Path], typer.Option("--questions-file", help="File with one question per line")] = None,
    apply_url: Annotated[Optional[str], typer.Option("--apply-url", "-a", help="URL of the application form (if questions are on a separate page)")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c", help="Company name (for output naming)")] = None,
    role: Annotated[Optional[str], typer.Option("--role", help="Role title (for output naming)")] = None,
) -> None:
    """Tailor your resume for a specific job."""
    ensure_dirs()
    cfg = load_config()

    # Validate at least one JD source
    if not any([job, job_url, job_file]):
        console.print("[red]Provide one of: --job, --job-url, or --job-file[/red]")
        raise typer.Exit(1)

    # Check base resume
    base_resume_path = DATA_DIR / "base_resume.docx"
    if not base_resume_path.exists():
        console.print(
            "[red]No base resume found.[/red] "
            "Run `resume-tailor setup --resume <path>` first."
        )
        raise typer.Exit(1)

    api_key = get_anthropic_api_key()
    client = Anthropic(api_key=api_key)

    # Collect questions
    questions: list[str] = list(question or [])
    if questions_file and Path(questions_file).exists():
        lines = Path(questions_file).read_text().splitlines()
        questions += [l.strip() for l in lines if l.strip()]

    # Step 0: Ingest job description
    from .job_analyzer import ingest_job_description, extract_questions
    console.print("[blue]Ingesting job description...[/blue]")
    jd_text = ingest_job_description(
        text=job,
        url=job_url,
        file=str(job_file) if job_file else None,
    )
    jd_source = job_url or (str(job_file) if job_file else "text input")

    # Auto-extract questions from JD page + optional separate apply URL
    console.print("[blue]Scanning for application questions...[/blue]")
    scraped_questions = extract_questions(jd_text, client)

    if apply_url:
        console.print(f"[blue]Scraping application form: {apply_url}[/blue]")
        try:
            apply_text = ingest_job_description(url=apply_url)
            form_questions = extract_questions(apply_text, client)
            scraped_questions = scraped_questions + [q for q in form_questions if q not in scraped_questions]
        except Exception as e:
            console.print(f"[yellow]Could not scrape apply URL: {e}[/yellow]")

    if scraped_questions:
        console.print(f"[green]Found {len(scraped_questions)} question(s) in job posting:[/green]")
        for q in scraped_questions:
            console.print(f"  [dim]• {q}[/dim]")
    # Manual -q flags take precedence; scraped ones fill the rest
    all_question_texts = {q.strip() for q in questions}
    for q in scraped_questions:
        if q.strip() not in all_question_texts:
            questions.append(q)
            all_question_texts.add(q.strip())

    # Fetch GitHub and LinkedIn in parallel (independent network calls)
    from .github_client import fetch_github_profile
    from .linkedin_client import fetch_linkedin_data

    github_profile = None
    linkedin_data = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        gh_future = (
            executor.submit(fetch_github_profile, cfg.github_username, cfg.github_token)
            if cfg.github_username else None
        )
        li_future = executor.submit(fetch_linkedin_data, cfg.linkedin_username, client)

        if gh_future:
            try:
                github_profile = gh_future.result()
            except Exception as e:
                console.print(f"[yellow]GitHub fetch failed: {e}[/yellow]")
        try:
            linkedin_data = li_future.result()
        except Exception as e:
            console.print(f"[yellow]LinkedIn fetch failed: {e}[/yellow]")

    # Parse base resume
    from .resume_processor import read_resume, check_ats_format
    console.print("[blue]Reading base resume...[/blue]")
    resume_doc = read_resume(base_resume_path, client)

    ats_warnings = check_ats_format(base_resume_path)
    for w in ats_warnings:
        console.print(f"[yellow]ATS Warning: {w}[/yellow]")

    # Run the 7-step pipeline
    from .claude_engine import run_pipeline
    tailored, answers, gap_report = run_pipeline(
        resume=resume_doc,
        jd_text=jd_text,
        questions=questions,
        github=github_profile,
        linkedin=linkedin_data,
        client=client,
    )

    # Save outputs
    from .output_manager import save_run, _make_run_id
    run_id = _make_run_id(company, role)
    run_dir = save_run(
        run_id=run_id,
        jd_text=jd_text,
        tailored=tailored,
        answers=answers,
        gap_report=gap_report,
        original_docx=base_resume_path,
        company=company,
        role=role,
        jd_source=jd_source,
    )

    console.print(
        f"\n[bold green]Done![/bold green] "
        f"Keyword match score: [bold]{tailored.keyword_match_score}/100[/bold]\n"
        f"Run `resume-tailor review` to approve or reject this tailoring."
    )


# ── review ─────────────────────────────────────────────────────────────────────

@app.command()
def review(
    run_id: Annotated[Optional[str], typer.Argument(help="Run ID to review (default: latest)")] = None,
) -> None:
    """Review a tailored resume and approve or reject it."""
    from .output_manager import review_run
    review_run(run_id)


# ── history ────────────────────────────────────────────────────────────────────

@app.command()
def history() -> None:
    """List all past runs."""
    from .output_manager import list_runs
    list_runs()


# ── config show ────────────────────────────────────────────────────────────────

config_app = typer.Typer(help="Manage configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    cfg = load_config()
    _show_config(cfg)


def _show_config(cfg: AppConfig) -> None:
    from rich.table import Table
    table = Table(title="Configuration")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("github_username", cfg.github_username or "[dim]not set[/dim]")
    table.add_row("linkedin_username", cfg.linkedin_username or "[dim]not set[/dim]")
    table.add_row("base_resume_path", cfg.base_resume_path)
    table.add_row("github_token", "[dim]set[/dim]" if cfg.github_token else "[dim]not set[/dim]")
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

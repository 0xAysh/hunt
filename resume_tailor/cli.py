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
    resume: Annotated[Optional[Path], typer.Option("--resume", "-r", help="Base DOCX or PDF resume path")] = None,
    github_token: Annotated[Optional[str], typer.Option("--github-token", help="GitHub PAT (stored in env)")] = None,
    target_roles: Annotated[Optional[str], typer.Option("--target-roles", help="Comma-separated target roles")] = None,
    salary_range: Annotated[Optional[str], typer.Option("--salary-range", help='e.g. "$150k-$200k"')] = None,
    remote_preference: Annotated[Optional[str], typer.Option("--remote", help="remote | hybrid | onsite | any")] = None,
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
    if target_roles:
        cfg.target_roles = [r.strip() for r in target_roles.split(",")]
    if salary_range:
        cfg.salary_range = salary_range
    if remote_preference:
        cfg.remote_preference = remote_preference

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
            marker.touch()
            console.print(f"[green]PDF converted and saved as {dest}[/green]")
        else:
            shutil.copy2(str(src), str(dest))
            marker.unlink(missing_ok=True)
            console.print(f"[green]Base resume copied to {dest}[/green]")

        cfg.base_resume_path = str(dest)

    save_config(cfg)
    console.print("[green]Configuration saved.[/green]")
    _show_config(cfg)


# ── evaluate ───────────────────────────────────────────────────────────────────

@app.command()
def evaluate(
    job: Annotated[Optional[str], typer.Option("--job", "-j", help="Job description as text")] = None,
    job_url: Annotated[Optional[str], typer.Option("--job-url", "-u", help="URL to job posting")] = None,
    job_file: Annotated[Optional[Path], typer.Option("--job-file", "-f", help="Path to job description file")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c")] = None,
    role: Annotated[Optional[str], typer.Option("--role")] = None,
) -> None:
    """Evaluate a job offer fit — score before spending time tailoring."""
    ensure_dirs()

    if not any([job, job_url, job_file]):
        console.print("[red]Provide one of: --job, --job-url, or --job-file[/red]")
        raise typer.Exit(1)

    cfg = load_config()
    api_key = get_anthropic_api_key()
    client = Anthropic(api_key=api_key)

    from .job_analyzer import ingest_job_description
    from .evaluator import evaluate_offer, format_evaluation_report
    from .claude_engine import _build_profile_context
    from .github_client import fetch_github_profile
    from .linkedin_client import fetch_linkedin_data
    from .resume_processor import read_resume

    console.print("[blue]Ingesting job description...[/blue]")
    jd_text = ingest_job_description(text=job, url=job_url, file=str(job_file) if job_file else None)

    base_resume_path = DATA_DIR / "base_resume.docx"
    if not base_resume_path.exists():
        console.print("[red]No base resume found. Run `resume-tailor setup --resume <path>` first.[/red]")
        raise typer.Exit(1)

    resume_doc = read_resume(base_resume_path, client)

    github_profile = None
    linkedin_data = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        gh_future = executor.submit(fetch_github_profile, cfg.github_username, cfg.github_token) if cfg.github_username else None
        li_future = executor.submit(fetch_linkedin_data, cfg.linkedin_username, client)
        if gh_future:
            try:
                github_profile = gh_future.result()
            except Exception:
                pass
        try:
            linkedin_data = li_future.result()
        except Exception:
            pass

    profile_context = _build_profile_context(resume_doc, github_profile, linkedin_data)
    evaluation = evaluate_offer(jd_text, profile_context, client, cfg)

    score = evaluation.score
    rec_color = {"apply": "green", "consider": "yellow", "skip": "red"}.get(score.recommendation, "white")
    console.print(f"\n[bold]Score:[/bold] {score.global_score:.1f}/5 — [{rec_color}]{score.recommendation.upper()}[/{rec_color}]")
    console.print(f"[dim]{score.reasoning}[/dim]\n")

    report = format_evaluation_report(evaluation, company, role, job_url)

    # Save report to a temp file if run_id available, else just print
    from .output_manager import _make_run_id
    run_id = _make_run_id(company, role)
    run_dir = DATA_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation.md").write_text(report)
    console.print(f"[dim]Evaluation saved to {run_dir / 'evaluation.md'}[/dim]")

    if score.global_score < 3.5:
        console.print(
            f"\n[yellow]Score {score.global_score:.1f} is below 3.5 — this role may not be worth applying to.[/yellow]"
        )


# ── run ────────────────────────────────────────────────────────────────────────

@app.command()
def run(
    job: Annotated[Optional[str], typer.Option("--job", "-j", help="Job description as text")] = None,
    job_url: Annotated[Optional[str], typer.Option("--job-url", "-u", help="URL to job posting")] = None,
    job_file: Annotated[Optional[Path], typer.Option("--job-file", "-f", help="Path to job description file")] = None,
    question: Annotated[Optional[list[str]], typer.Option("--question", "-q", help="Application question (repeatable)")] = None,
    questions_file: Annotated[Optional[Path], typer.Option("--questions-file")] = None,
    apply_url: Annotated[Optional[str], typer.Option("--apply-url", "-a", help="URL of the application form")] = None,
    company: Annotated[Optional[str], typer.Option("--company", "-c")] = None,
    role: Annotated[Optional[str], typer.Option("--role")] = None,
    skip_eval: Annotated[bool, typer.Option("--skip-eval", help="Skip offer evaluation step")] = False,
) -> None:
    """Tailor your resume for a specific job."""
    ensure_dirs()
    cfg = load_config()

    if not any([job, job_url, job_file]):
        console.print("[red]Provide one of: --job, --job-url, or --job-file[/red]")
        raise typer.Exit(1)

    base_resume_path = DATA_DIR / "base_resume.docx"
    if not base_resume_path.exists():
        console.print("[red]No base resume found. Run `resume-tailor setup --resume <path>` first.[/red]")
        raise typer.Exit(1)

    api_key = get_anthropic_api_key()
    client = Anthropic(api_key=api_key)

    # Collect questions
    questions: list[str] = list(question or [])
    if questions_file and Path(questions_file).exists():
        lines = Path(questions_file).read_text().splitlines()
        questions += [l.strip() for l in lines if l.strip()]

    from .job_analyzer import ingest_job_description, extract_questions
    console.print("[blue]Ingesting job description...[/blue]")
    jd_text = ingest_job_description(text=job, url=job_url, file=str(job_file) if job_file else None)
    jd_source = job_url or (str(job_file) if job_file else "text input")

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
        console.print(f"[green]Found {len(scraped_questions)} question(s) in job posting.[/green]")
    all_question_texts = {q.strip() for q in questions}
    for q in scraped_questions:
        if q.strip() not in all_question_texts:
            questions.append(q)
            all_question_texts.add(q.strip())

    from .github_client import fetch_github_profile
    from .linkedin_client import fetch_linkedin_data
    from .claude_engine import _build_profile_context

    github_profile = None
    linkedin_data = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        gh_future = executor.submit(fetch_github_profile, cfg.github_username, cfg.github_token) if cfg.github_username else None
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

    # Evaluate offer first (unless skipped)
    evaluation = None
    evaluation_report = None
    offer_score = None
    recommendation = None

    if not skip_eval:
        from .evaluator import evaluate_offer, format_evaluation_report
        profile_context = _build_profile_context(
            __import__("resume_tailor.resume_processor", fromlist=["read_resume"]).read_resume(base_resume_path, client),
            github_profile, linkedin_data,
        )
        evaluation = evaluate_offer(jd_text, profile_context, client, cfg)
        offer_score = evaluation.score.global_score
        recommendation = evaluation.score.recommendation
        evaluation_report = format_evaluation_report(evaluation, company, role, jd_source)

        rec_color = {"apply": "green", "consider": "yellow", "skip": "red"}.get(recommendation, "white")
        console.print(
            f"\n[bold]Offer score:[/bold] {offer_score:.1f}/5 — [{rec_color}]{recommendation.upper()}[/{rec_color}]"
        )
        console.print(f"[dim]{evaluation.score.reasoning}[/dim]")

        if offer_score < 3.5:
            proceed = typer.confirm(
                f"\nScore {offer_score:.1f} is below 3.5. Proceed with tailoring anyway?",
                default=False,
            )
            if not proceed:
                console.print("[yellow]Skipping tailoring. Run `resume-tailor evaluate` to see full report.[/yellow]")
                raise typer.Exit(0)

    from .resume_processor import read_resume, check_ats_format
    console.print("[blue]Reading base resume...[/blue]")
    resume_doc = read_resume(base_resume_path, client)

    ats_warnings = check_ats_format(base_resume_path)
    for w in ats_warnings:
        console.print(f"[yellow]ATS Warning: {w}[/yellow]")

    from .claude_engine import run_pipeline
    tailored, answers, gap_report = run_pipeline(
        resume=resume_doc,
        jd_text=jd_text,
        questions=questions,
        github=github_profile,
        linkedin=linkedin_data,
        client=client,
    )

    from .output_manager import save_run, _make_run_id
    run_id = _make_run_id(company, role)
    save_run(
        run_id=run_id,
        jd_text=jd_text,
        tailored=tailored,
        answers=answers,
        gap_report=gap_report,
        original_docx=base_resume_path,
        company=company,
        role=role,
        jd_source=jd_source,
        evaluation_report=evaluation_report,
        offer_score=offer_score,
        recommendation=recommendation,
    )

    console.print(
        f"\n[bold green]Done![/bold green] "
        f"Keyword match: [bold]{tailored.keyword_match_score}/100[/bold]"
        + (f" · Offer score: [bold]{offer_score:.1f}/5[/bold]" if offer_score else "")
    )
    console.print("Run `resume-tailor review` to approve or reject this tailoring.")


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


# ── negotiate ──────────────────────────────────────────────────────────────────

@app.command()
def negotiate(
    role: Annotated[str, typer.Option("--role", "-r", help="Job role title")] = "",
    company: Annotated[Optional[str], typer.Option("--company", "-c")] = None,
    location: Annotated[Optional[str], typer.Option("--location", "-l")] = None,
    run_id: Annotated[Optional[str], typer.Option("--run-id", help="Generate scripts for a previous run")] = None,
) -> None:
    """Research market compensation and generate negotiation scripts."""
    ensure_dirs()
    cfg = load_config()
    client = Anthropic(api_key=get_anthropic_api_key())

    from .negotiation import research_compensation, generate_negotiation_scripts, format_negotiation_report

    effective_role = role or "Software Engineer"
    effective_company = company

    if run_id:
        run_dir = DATA_DIR / "runs" / run_id
        meta_path = run_dir / "run_meta.json"
        if meta_path.exists():
            from .models import RunMeta
            meta = RunMeta.model_validate_json(meta_path.read_text())
            effective_role = meta.role or effective_role
            effective_company = meta.company or effective_company

    comp = research_compensation(effective_role, effective_company, location, client)

    scripts = generate_negotiation_scripts(
        comp_research=comp,
        user_salary_target=cfg.salary_range,
        candidate_name=None,
        profile_summary=f"Targeting {effective_role}" + (f" at {effective_company}" if effective_company else ""),
        client=client,
    )

    report = format_negotiation_report(comp, scripts, effective_company, effective_role)

    # Print summary
    if comp.salary_range_mid:
        console.print(f"\n[bold]Market P50:[/bold] ${comp.salary_range_mid:,}")
    console.print(f"[bold]{len(scripts)} negotiation scripts generated.[/bold]")

    # Save
    if run_id:
        run_dir = DATA_DIR / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "negotiation.md"
    else:
        out_path = DATA_DIR / f"negotiation_{effective_role.replace(' ', '_').lower()}.md"

    out_path.write_text(report)
    console.print(f"[green]Playbook saved to:[/green] {out_path}")


# ── stories ────────────────────────────────────────────────────────────────────

stories_app = typer.Typer(help="Manage the interview story bank.")
app.add_typer(stories_app, name="stories")


@stories_app.command("list")
def stories_list() -> None:
    """List all stories in the bank."""
    from .story_bank import load_story_bank
    from rich.table import Table

    bank = load_story_bank()
    if not bank:
        console.print("[yellow]No stories yet. They accumulate as you run tailoring jobs.[/yellow]")
        return

    table = Table(title=f"Interview Story Bank ({len(bank)} stories)")
    table.add_column("Theme", style="cyan")
    table.add_column("Title")
    table.add_column("Used", justify="right")
    table.add_column("Companies")
    for s in bank:
        table.add_row(s.theme, s.title[:60], str(s.times_used), ", ".join(s.source_companies[:2]))
    console.print(table)


@stories_app.command("export")
def stories_export(
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("data/story_bank.md"),
) -> None:
    """Export all stories as a markdown file for interview prep."""
    from .story_bank import export_story_bank_markdown
    md = export_story_bank_markdown()
    Path(output).write_text(md)
    console.print(f"[green]Exported to {output}[/green]")


@stories_app.command("match")
def stories_match(
    job_url: Annotated[Optional[str], typer.Option("--job-url", "-u")] = None,
    job: Annotated[Optional[str], typer.Option("--job", "-j")] = None,
) -> None:
    """Show which stored stories match a specific job's requirements."""
    if not job_url and not job:
        console.print("[red]Provide --job-url or --job[/red]")
        raise typer.Exit(1)

    from .job_analyzer import ingest_job_description
    from .story_bank import find_relevant_stories

    client = Anthropic(api_key=get_anthropic_api_key())
    jd_text = ingest_job_description(text=job, url=job_url)

    # Quick keyword extract
    from .claude_engine import analyze_job_description
    job_analysis = analyze_job_description(jd_text, client)
    requirements = job_analysis.required_skills + job_analysis.responsibilities[:5]

    stories = find_relevant_stories(requirements, client)
    if not stories:
        console.print("[yellow]No matching stories found in bank.[/yellow]")
        return

    console.print(f"\n[bold]{len(stories)} stories matched:[/bold]\n")
    for s in stories:
        console.print(f"  [{s.theme}] [bold]{s.title}[/bold]")
        console.print(f"  [dim]{s.action[:120]}...[/dim]\n")


# ── scan ──────────────────────────────────────────────────────────────────────

@app.command()
def scan(
    level: Annotated[Optional[list[int]], typer.Option("--level", "-l", help="Scan levels: 1=Playwright, 2=API, 3=WebSearch")] = None,
    company_filter: Annotated[Optional[str], typer.Option("--company")] = None,
) -> None:
    """Scan configured job portals for new openings."""
    from .scanner import run_scan, print_scan_summary

    levels = level or [1, 2, 3]
    console.print(f"[blue]Running portal scan (levels: {levels})...[/blue]")
    summary = run_scan(levels=levels)
    print_scan_summary(summary)


@app.command()
def pipeline(
    limit: Annotated[Optional[int], typer.Option("--limit", "-n", help="Max jobs to process")] = None,
) -> None:
    """Show or process the job pipeline inbox."""
    from .scanner import load_pipeline

    jobs = load_pipeline()
    if not jobs:
        console.print("[yellow]Pipeline is empty. Run `resume-tailor scan` to discover new offers.[/yellow]")
        return

    from rich.table import Table
    table = Table(title=f"Pipeline — {len(jobs)} pending")
    table.add_column("#")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("URL")
    for i, j in enumerate(jobs, 1):
        table.add_row(str(i), j["company"], j["role"], j["url"][:60])
    console.print(table)
    console.print("\nRun [bold]resume-tailor batch --from-pipeline[/bold] to process all pending offers.")


# ── batch ─────────────────────────────────────────────────────────────────────

@app.command()
def batch(
    urls: Annotated[Optional[list[str]], typer.Option("--urls", "-u", help="Job URLs to process")] = None,
    from_pipeline: Annotated[bool, typer.Option("--from-pipeline", help="Process pending pipeline jobs")] = False,
    limit: Annotated[Optional[int], typer.Option("--limit", "-n")] = None,
    parallel: Annotated[int, typer.Option("--parallel", "-p")] = 3,
    min_score: Annotated[float, typer.Option("--min-score")] = 3.5,
    evaluate_only: Annotated[bool, typer.Option("--evaluate-only")] = False,
    resume_id: Annotated[Optional[str], typer.Option("--resume")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Process multiple job offers in parallel (evaluate + tailor)."""
    from .batch_processor import run_batch, list_batches
    from .scanner import load_pipeline

    if not urls and not from_pipeline and not resume_id:
        console.print("[red]Provide --urls, --from-pipeline, or --resume <batch_id>[/red]")
        raise typer.Exit(1)

    target_urls: list[str] = list(urls or [])

    if from_pipeline:
        pipeline_jobs = load_pipeline()
        if limit:
            pipeline_jobs = pipeline_jobs[:limit]
        target_urls += [j["url"] for j in pipeline_jobs]

    if dry_run:
        console.print(f"[dim]Dry run — would process {len(target_urls)} URLs:[/dim]")
        for u in target_urls:
            console.print(f"  {u}")
        return

    run_batch(
        urls=target_urls,
        parallel=parallel,
        min_score=min_score,
        evaluate_only=evaluate_only,
        batch_id=resume_id,
    )


@app.command("batch-history")
def batch_history() -> None:
    """List past batch runs."""
    from .batch_processor import list_batches
    list_batches()


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
    table.add_row("target_roles", ", ".join(cfg.target_roles) or "[dim]not set[/dim]")
    table.add_row("salary_range", cfg.salary_range or "[dim]not set[/dim]")
    table.add_row("remote_preference", cfg.remote_preference or "[dim]not set[/dim]")
    table.add_row("pdf_accent_color", cfg.pdf_accent_color)
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

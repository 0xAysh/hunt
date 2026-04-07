"""Write run outputs and manage the approval/promotion flow."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .models import QuestionAnswer, RunMeta, TailoredResume
from .resume_processor import export_pdf, write_docx

console = Console()

RUNS_DIR = Path(__file__).parent.parent / "data" / "runs"
DATA_DIR = Path(__file__).parent.parent / "data"


def _make_run_id(company: Optional[str], role: Optional[str]) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    slug_parts = [date]
    if company:
        slug_parts.append(re.sub(r"[^a-z0-9]+", "_", company.lower()).strip("_"))
    if role:
        slug_parts.append(re.sub(r"[^a-z0-9]+", "_", role.lower()).strip("_")[:20])
    return "_".join(slug_parts)


def save_run(
    run_id: str,
    jd_text: str,
    tailored: TailoredResume,
    answers: list[QuestionAnswer],
    gap_report: str,
    original_docx: Path,
    company: Optional[str] = None,
    role: Optional[str] = None,
    jd_source: Optional[str] = None,
) -> Path:
    """Write all run outputs to data/runs/<run_id>/. Returns run directory."""
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Job description
    (run_dir / "job_description.txt").write_text(jd_text)

    # Gap analysis report
    (run_dir / "gap_analysis.md").write_text(gap_report)

    # Answers
    if answers:
        answers_md = "# Application Question Answers\n\n"
        for qa in answers:
            answers_md += f"## {qa.question}\n\n{qa.answer}\n\n---\n\n"
        (run_dir / "answers.md").write_text(answers_md)

    # Tailored DOCX
    docx_path = run_dir / "tailored_resume.docx"
    write_docx(original_docx, tailored, docx_path)

    # PDF export
    pdf_path = export_pdf(docx_path, run_dir)

    # Run metadata
    meta = RunMeta(
        run_id=run_id,
        company=company,
        role=role,
        created_at=datetime.now(timezone.utc),
        job_description_source=jd_source,
        keyword_match_score=tailored.keyword_match_score,
    )
    (run_dir / "run_meta.json").write_text(meta.model_dump_json(indent=2))

    console.print(f"\n[green]Run saved to:[/green] {run_dir}")
    if pdf_path:
        console.print(f"[green]PDF:[/green] {pdf_path}")

    return run_dir


def _open_file(path: Path) -> None:
    """Open a file with the system default application."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", str(path)])
        elif system == "Windows":
            os.startfile(str(path))
    except Exception:
        pass


def review_run(run_id: Optional[str] = None) -> None:
    """Interactive review: show gap analysis, open files, prompt approve/reject/edit."""
    # Find the run directory
    if run_id:
        run_dir = RUNS_DIR / run_id
    else:
        # Latest run
        runs = sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        runs = [r for r in runs if r.is_dir()]
        if not runs:
            console.print("[red]No runs found. Run `resume-tailor run` first.[/red]")
            return
        run_dir = runs[0]

    if not run_dir.exists():
        console.print(f"[red]Run directory not found: {run_dir}[/red]")
        return

    meta_path = run_dir / "run_meta.json"
    meta = RunMeta.model_validate_json(meta_path.read_text()) if meta_path.exists() else None

    console.print(Panel(f"[bold]Reviewing run:[/bold] {run_dir.name}", style="blue"))

    # Show gap analysis inline
    gap_path = run_dir / "gap_analysis.md"
    if gap_path.exists():
        console.print(Markdown(gap_path.read_text()))

    # Open preferred file: PDF first, DOCX as fallback
    docx_path = run_dir / "tailored_resume.docx"
    pdf_path = run_dir / "tailored_resume.pdf"

    view_path = pdf_path if pdf_path.exists() else (docx_path if docx_path.exists() else None)
    if view_path:
        console.print(f"\n[dim]Opening {view_path.name}...[/dim]")
        _open_file(view_path)

    # Prompt
    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Approve — promote this resume as new base", value="a"),
            questionary.Choice("Reject — keep outputs, base unchanged", value="r"),
            questionary.Choice("Edit — open in editor, then re-review", value="e"),
        ],
    ).ask()

    if action == "a":
        _approve_run(run_dir, meta)
    elif action == "r":
        console.print("[yellow]Run rejected. Outputs preserved in run directory.[/yellow]")
    elif action == "e":
        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, str(docx_path)])
        console.print("[dim]File saved. Re-running review...[/dim]")
        review_run(run_dir.name)


def _approve_run(run_dir: Path, meta: Optional[RunMeta]) -> None:
    """Promote tailored resume to base, archive old base."""
    base_path = DATA_DIR / "base_resume.docx"
    tailored_path = run_dir / "tailored_resume.docx"

    if not tailored_path.exists():
        console.print("[red]Tailored DOCX not found in run directory.[/red]")
        return

    # Archive current base
    if base_path.exists():
        archive_name = f"base_resume_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        archive_path = DATA_DIR / archive_name
        shutil.copy2(str(base_path), str(archive_path))
        console.print(f"[dim]Old base archived as {archive_name}[/dim]")

    # Promote
    shutil.copy2(str(tailored_path), str(base_path))

    # Update meta
    if meta:
        meta.approved = True
        meta.approved_at = datetime.now(timezone.utc)
        (run_dir / "run_meta.json").write_text(meta.model_dump_json(indent=2))

    console.print(
        "[green bold]✓ Approved![/green bold] "
        f"Tailored resume is now your base resume at [bold]{base_path}[/bold]\n"
        "Future runs will build on this improved version."
    )


def list_runs() -> None:
    """Print a table of all past runs."""
    if not RUNS_DIR.exists():
        console.print("[yellow]No runs directory found.[/yellow]")
        return

    runs = sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    runs = [r for r in runs if r.is_dir()]

    if not runs:
        console.print("[yellow]No runs yet. Use `resume-tailor run` to create one.[/yellow]")
        return

    from rich.table import Table
    table = Table(title="Run History", show_lines=True)
    table.add_column("Run ID", style="cyan")
    table.add_column("Company")
    table.add_column("Score")
    table.add_column("Approved", style="green")
    table.add_column("Date")

    for run_dir in runs:
        meta_path = run_dir / "run_meta.json"
        if meta_path.exists():
            try:
                meta = RunMeta.model_validate_json(meta_path.read_text())
                table.add_row(
                    meta.run_id,
                    meta.company or "—",
                    str(meta.keyword_match_score),
                    "✓" if meta.approved else "—",
                    meta.created_at.strftime("%Y-%m-%d %H:%M"),
                )
            except Exception:
                table.add_row(run_dir.name, "—", "—", "—", "—")
        else:
            table.add_row(run_dir.name, "—", "—", "—", "—")

    console.print(table)

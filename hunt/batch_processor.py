"""Parallel batch processing of multiple job offers."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anthropic import Anthropic
from rich.console import Console
from rich.table import Table

from .config import get_anthropic_api_key, load_config, DATA_DIR, BATCHES_DIR
from .models import AppConfig, BatchJob, BatchState

console = Console()

# Global lock for Playwright-based JD ingestion (not thread-safe)
_playwright_lock = threading.Lock()


def _state_path(batch_id: str) -> Path:
    return BATCHES_DIR / batch_id / "state.json"


def save_state(state: BatchState) -> None:
    path = _state_path(state.batch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2, mode="json"))


def load_state(batch_id: str) -> Optional[BatchState]:
    path = _state_path(batch_id)
    if not path.exists():
        return None
    try:
        return BatchState.model_validate_json(path.read_text())
    except Exception:
        return None


def _process_single_job(
    job: BatchJob,
    client: Anthropic,
    cfg: AppConfig,
    state: BatchState,
    state_lock: threading.Lock,
    profile_context: str,
    base_resume_path: Path,
    resume_doc,
    github_profile,
    linkedin_data,
) -> BatchJob:
    """Process one job offer end-to-end. Runs in a worker thread.

    Profile context, resume, and API profiles are pre-built once by run_batch()
    and passed in — avoids redundant work per job.
    """
    from .job_analyzer import ingest_job_description
    from .evaluator import evaluate_offer
    from .claude_engine import run_pipeline
    from .output_manager import save_run, _make_run_id

    job.status = "processing"
    job.started_at = datetime.now(timezone.utc)

    with state_lock:
        save_state(state)

    try:
        # Phase 1: Ingest JD (Playwright may be needed — serialize with lock)
        with _playwright_lock:
            jd_text = ingest_job_description(url=job.url)

        # Phase 2: Evaluate offer
        evaluation = evaluate_offer(jd_text, profile_context, client, cfg)
        job.score = evaluation.score.global_score
        job.recommendation = evaluation.score.recommendation

        # Phase 3: Tailor if score meets threshold
        if not state.min_score_for_tailoring or job.score >= state.min_score_for_tailoring:
            tailored, answers, gap_report = run_pipeline(
                resume=resume_doc,
                jd_text=jd_text,
                questions=[],
                github=github_profile,
                linkedin=linkedin_data,
                client=client,
            )

            run_id = _make_run_id(job.company, job.role)
            save_run(
                run_id=run_id,
                jd_text=jd_text,
                tailored=tailored,
                answers=answers,
                gap_report=gap_report,
                original_docx=base_resume_path,
                company=job.company,
                role=job.role,
                jd_source=job.url,
            )
            job.run_id = run_id

        job.status = "completed"

    except Exception as e:
        job.status = "failed"
        job.error = str(e)

    job.completed_at = datetime.now(timezone.utc)

    with state_lock:
        # Update job in state
        for i, j in enumerate(state.jobs):
            if j.id == job.id:
                state.jobs[i] = job
                break
        save_state(state)

    return job


def run_batch(
    urls: list[str],
    parallel: int = 3,
    min_score: float = 3.5,
    evaluate_only: bool = False,
    batch_id: Optional[str] = None,
    retry_failed: bool = False,
) -> BatchState:
    """Process multiple job offers, optionally in parallel.

    Two-phase approach for thread safety:
    Phase 1 (sequential): JD ingestion via Playwright
    Phase 2 (parallel): Evaluation + tailoring (Claude API, no browser)
    """
    from .claude_engine import _build_profile_context
    from .github_client import fetch_github_profile
    from .linkedin_client import fetch_linkedin_data
    from .resume_processor import read_resume

    cfg = load_config()
    client = Anthropic(api_key=get_anthropic_api_key())

    # Build shared context once — not per job
    base_resume_path = DATA_DIR / "base_resume.docx"
    if not base_resume_path.exists():
        raise FileNotFoundError("base_resume.docx not found. Run `hunt setup --resume <path>` first.")

    console.print("[blue]Building profile context (shared across all jobs)...[/blue]")
    resume_doc = read_resume(base_resume_path, client)

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

    profile_context = _build_profile_context(resume_doc, github_profile, linkedin_data)

    # Load or create state
    if batch_id:
        state = load_state(batch_id)
        if state is None:
            console.print(f"[red]Batch {batch_id} not found.[/red]")
            return BatchState(batch_id=batch_id)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_id = f"batch_{ts}"
        state = BatchState(
            batch_id=batch_id,
            parallel_workers=parallel,
            min_score_for_tailoring=0.0 if evaluate_only else min_score,
        )
        state.jobs = [
            BatchJob(id=i + 1, url=url)
            for i, url in enumerate(urls)
        ]
        save_state(state)

    if retry_failed:
        for job in state.jobs:
            if job.status == "failed":
                job.status = "pending"
                job.error = None

    pending = [j for j in state.jobs if j.status == "pending"]
    if not pending:
        console.print("[yellow]No pending jobs in this batch.[/yellow]")
        return state

    console.print(
        f"\n[bold]Batch:[/bold] {batch_id} | "
        f"[bold]Workers:[/bold] {parallel} | "
        f"[bold]Jobs:[/bold] {len(pending)}\n"
    )

    state_lock = threading.Lock()

    _REC_EMOJI = {"apply": "✅", "consider": "🟡", "skip": "❌"}

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(
                _process_single_job,
                job, client, cfg, state, state_lock,
                profile_context, base_resume_path, resume_doc,
                github_profile, linkedin_data,
            ): job
            for job in pending
        }

        for future in as_completed(futures):
            job = future.result()
            rec_icon = _REC_EMOJI.get(job.recommendation or "", "·")
            if job.status == "completed":
                score_str = f"{job.score:.1f}/5" if job.score else "—"
                console.print(
                    f"  [{job.id}] {job.company or job.url[:40]} — "
                    f"{score_str} {rec_icon} {job.recommendation or ''}"
                )
            else:
                console.print(f"  [{job.id}] FAILED: {job.error or 'unknown error'}")

    _print_batch_summary(state)
    return state


def _print_batch_summary(state: BatchState) -> None:
    completed = [j for j in state.jobs if j.status == "completed"]
    failed = [j for j in state.jobs if j.status == "failed"]

    console.print(f"\n[bold]Batch Complete[/bold] — {len(completed)}/{len(state.jobs)} succeeded\n")

    apply_jobs = [j for j in completed if j.recommendation == "apply"]
    consider_jobs = [j for j in completed if j.recommendation == "consider"]
    skip_jobs = [j for j in completed if j.recommendation == "skip"]

    if apply_jobs:
        console.print("[green bold]Recommended (APPLY):[/green bold]")
        for j in sorted(apply_jobs, key=lambda x: x.score or 0, reverse=True):
            console.print(f"  {j.score:.1f}/5  {j.company or ''} — {j.role or j.url[:50]}")

    if consider_jobs:
        console.print("\n[yellow]Worth considering:[/yellow]")
        for j in sorted(consider_jobs, key=lambda x: x.score or 0, reverse=True):
            console.print(f"  {j.score:.1f}/5  {j.company or ''} — {j.role or j.url[:50]}")

    if skip_jobs:
        console.print("\n[dim]Skip:[/dim]")
        for j in sorted(skip_jobs, key=lambda x: x.score or 0, reverse=True):
            console.print(f"  {j.score:.1f}/5  {j.company or ''} — {j.role or j.url[:50]}")

    if failed:
        console.print(f"\n[red]Failed ({len(failed)}):[/red]")
        for j in failed:
            console.print(f"  [{j.id}] {j.url[:60]} — {j.error or 'unknown'}")


def list_batches() -> None:
    """Print a table of past batch runs."""
    if not BATCHES_DIR.exists():
        console.print("[yellow]No batch runs yet.[/yellow]")
        return

    dirs = sorted(BATCHES_DIR.iterdir(), reverse=True)
    if not dirs:
        console.print("[yellow]No batch runs yet.[/yellow]")
        return

    table = Table(title="Batch History")
    table.add_column("Batch ID", style="cyan")
    table.add_column("Jobs")
    table.add_column("Done")
    table.add_column("Apply")
    table.add_column("Date")

    for d in dirs[:20]:
        state = load_state(d.name)
        if not state:
            continue
        done = len([j for j in state.jobs if j.status == "completed"])
        apply_count = len([j for j in state.jobs if j.recommendation == "apply"])
        table.add_row(
            state.batch_id,
            str(len(state.jobs)),
            str(done),
            str(apply_count),
            state.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)

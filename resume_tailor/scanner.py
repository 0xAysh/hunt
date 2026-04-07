"""Multi-level portal scanner — discovers new job openings from configured sources."""
from __future__ import annotations

import csv
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import yaml
from rich.console import Console
from rich.table import Table

from .models import ScanResult, ScanSummary

console = Console()

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_PATH = DATA_DIR / "scan-history.tsv"
PIPELINE_PATH = DATA_DIR / "pipeline.md"
PORTALS_PATH = Path(__file__).parent.parent / "portals.yml"

# Regex to extract title and company from search result strings
_TITLE_COMPANY_RE = re.compile(
    r"^(.+?)(?:\s*[@|—–\-]\s*|\s+at\s+|\s*\|\s*)(.+?)$", re.IGNORECASE
)


# ── Config loading ────────────────────────────────────────────────────────────

def _load_portals_config() -> dict:
    if not PORTALS_PATH.exists():
        example = Path(__file__).parent.parent / "templates" / "portals.example.yml"
        if example.exists():
            console.print(
                f"[yellow]portals.yml not found. Copy the example: "
                f"cp templates/portals.example.yml portals.yml[/yellow]"
            )
        else:
            console.print("[yellow]portals.yml not found. Create it to configure scanning.[/yellow]")
        return {}
    with open(PORTALS_PATH) as f:
        return yaml.safe_load(f) or {}


# ── History management ────────────────────────────────────────────────────────

def _load_history() -> set[str]:
    """Return set of URLs already seen."""
    seen: set[str] = set()
    if not HISTORY_PATH.exists():
        return seen
    with open(HISTORY_PATH, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if "url" in row:
                seen.add(row["url"].strip())
    return seen


def _load_pipeline_urls() -> set[str]:
    """Return set of URLs already in pipeline.md."""
    if not PIPELINE_PATH.exists():
        return set()
    urls: set[str] = set()
    content = PIPELINE_PATH.read_text()
    for line in content.splitlines():
        # Lines like: - [ ] https://... | Company | Role
        match = re.search(r"https?://\S+", line)
        if match:
            urls.add(match.group(0).split("|")[0].strip())
    return urls


def _append_history(results: list[ScanResult]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not HISTORY_PATH.exists()
    with open(HISTORY_PATH, "a", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        if write_header:
            writer.writerow(["url", "first_seen", "source", "title", "company", "status"])
        for r in results:
            writer.writerow([r.url, r.scan_date, r.discovered_via, r.title, r.company, r.status])


def _append_pipeline(results: list[ScanResult]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PIPELINE_PATH.exists():
        PIPELINE_PATH.write_text("# Job Pipeline\n\n## Pending\n\n## Processed\n")

    content = PIPELINE_PATH.read_text()
    pending_section = "## Pending\n"
    new_lines = []
    for r in results:
        if r.status == "added":
            new_lines.append(f"- [ ] {r.url} | {r.company} | {r.title}")

    if new_lines:
        insert_after = content.find(pending_section)
        if insert_after != -1:
            insert_pos = insert_after + len(pending_section)
            addition = "\n".join(new_lines) + "\n"
            content = content[:insert_pos] + addition + content[insert_pos:]
            PIPELINE_PATH.write_text(content)


# ── Title filtering ───────────────────────────────────────────────────────────

def _passes_filter(title: str, title_filter: dict) -> bool:
    lower = title.lower()
    positive = [k.lower() for k in title_filter.get("positive", [])]
    negative = [k.lower() for k in title_filter.get("negative", [])]

    if positive and not any(kw in lower for kw in positive):
        return False
    if any(kw in lower for kw in negative):
        return False
    return True


# ── Level 1: Playwright direct navigation ────────────────────────────────────

def _scan_playwright(companies: list[dict], scan_date: str) -> list[ScanResult]:
    """Navigate each company's careers page with Playwright and extract job listings."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        console.print("[yellow]Playwright not available — skipping direct scan.[/yellow]")
        return []

    results: list[ScanResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for company_cfg in companies:
            if not company_cfg.get("enabled", True):
                continue
            careers_url = company_cfg.get("careers_url")
            if not careers_url:
                continue

            name = company_cfg.get("name", "Unknown")
            console.print(f"[dim]  Scanning {name}...[/dim]")

            try:
                page = browser.new_page()
                page.goto(careers_url, wait_until="domcontentloaded", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except PWTimeout:
                    pass

                # Extract all links that look like job postings
                links = page.eval_on_selector_all(
                    "a[href]",
                    """els => els.map(el => ({
                        text: el.innerText.trim(),
                        href: el.href
                    })).filter(l => l.text.length > 5 && l.text.length < 120)"""
                )

                for link in links:
                    text = link.get("text", "").strip()
                    href = link.get("href", "").strip()
                    if not text or not href:
                        continue
                    # Filter out nav/footer links — job links are usually longer paths
                    path = href.split("?")[0]
                    if len(path.split("/")) < 4:
                        continue
                    results.append(ScanResult(
                        title=text,
                        company=name,
                        url=href,
                        platform=company_cfg.get("platform"),
                        discovered_via="playwright",
                        scan_date=scan_date,
                    ))

                page.close()
                time.sleep(1.5)  # Polite delay

            except Exception as e:
                console.print(f"[yellow]  {name}: {e}[/yellow]")

        browser.close()

    return results


# ── Level 2: Greenhouse JSON API ──────────────────────────────────────────────

def _scan_greenhouse_api(companies: list[dict], scan_date: str) -> list[ScanResult]:
    """Fetch structured job data from the Greenhouse jobs API."""
    results: list[ScanResult] = []

    for company_cfg in companies:
        if not company_cfg.get("enabled", True):
            continue
        api_url = company_cfg.get("api")
        if not api_url:
            continue

        name = company_cfg.get("name", "Unknown")
        console.print(f"[dim]  API: {name}...[/dim]")

        try:
            resp = httpx.get(api_url, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                continue
            data = resp.json()
            jobs = data.get("jobs", data) if isinstance(data, dict) else data

            for job in (jobs if isinstance(jobs, list) else []):
                title = job.get("title") or job.get("name") or ""
                url = job.get("absolute_url") or job.get("url") or ""
                if title and url:
                    results.append(ScanResult(
                        title=title,
                        company=name,
                        url=url,
                        platform="greenhouse",
                        discovered_via="api",
                        scan_date=scan_date,
                    ))
        except Exception as e:
            console.print(f"[yellow]  {name} API: {e}[/yellow]")

    return results


# ── Level 3: Web search ───────────────────────────────────────────────────────

def _scan_websearch(queries: list[dict], scan_date: str) -> list[ScanResult]:
    """Use web search to discover jobs across job boards."""
    results: list[ScanResult] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    for query_cfg in queries:
        if not query_cfg.get("enabled", True):
            continue
        query = query_cfg.get("query", "")
        query_name = query_cfg.get("name", query[:40])
        if not query:
            continue

        console.print(f"[dim]  Search: {query_name}...[/dim]")

        try:
            resp = httpx.get(
                "https://www.google.com/search",
                params={"q": query, "num": "15", "hl": "en"},
                headers=headers,
                follow_redirects=True,
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "lxml")

            for result in soup.select("div.g"):
                title_el = result.select_one("h3")
                link_el = result.select_one("a[href]")
                if not title_el or not link_el:
                    continue

                raw_title = title_el.get_text(strip=True)
                url = link_el["href"]

                # Skip non-job URLs
                if not any(domain in url for domain in [
                    "ashbyhq.com", "greenhouse.io", "lever.co", "wellfound.com",
                    "workable.com", "jobs.", "careers.", "/careers", "/jobs",
                ]):
                    continue

                # Extract title and company
                match = _TITLE_COMPANY_RE.match(raw_title)
                if match:
                    title, company = match.group(1).strip(), match.group(2).strip()
                else:
                    title, company = raw_title, query_name

                results.append(ScanResult(
                    title=title,
                    company=company,
                    url=url,
                    platform=None,
                    discovered_via=f"websearch:{query_name}",
                    scan_date=scan_date,
                ))

            time.sleep(2)  # Polite delay between searches

        except Exception as e:
            console.print(f"[yellow]  Search '{query_name}': {e}[/yellow]")

    return results


# ── Main scan function ────────────────────────────────────────────────────────

def run_scan(levels: list[int] = [1, 2, 3]) -> ScanSummary:
    """Execute the full portal scan pipeline."""
    config = _load_portals_config()
    if not config:
        return ScanSummary(total_found=0, filtered=0, new_added=0)

    title_filter = config.get("title_filter", {})
    tracked_companies = config.get("tracked_companies", [])
    search_queries = config.get("search_queries", [])

    history_urls = _load_history()
    pipeline_urls = _load_pipeline_urls()
    seen_urls = history_urls | pipeline_urls

    scan_date = date.today().isoformat()
    all_results: list[ScanResult] = []

    if 1 in levels and tracked_companies:
        console.print("[blue]Level 1: Direct career page scan...[/blue]")
        all_results += _scan_playwright(tracked_companies, scan_date)

    if 2 in levels and tracked_companies:
        console.print("[blue]Level 2: API scan...[/blue]")
        all_results += _scan_greenhouse_api(tracked_companies, scan_date)

    if 3 in levels and search_queries:
        console.print("[blue]Level 3: Web search...[/blue]")
        all_results += _scan_websearch(search_queries, scan_date)

    # Dedup by URL within this scan's results
    seen_this_scan: set[str] = set()
    unique_results: list[ScanResult] = []
    for r in all_results:
        if r.url not in seen_this_scan:
            seen_this_scan.add(r.url)
            unique_results.append(r)

    # Apply title filter and dedup against history
    filtered: list[ScanResult] = []
    for r in unique_results:
        if r.url in seen_urls:
            r.status = "skipped_dup"
        elif not _passes_filter(r.title, title_filter):
            r.status = "skipped_title"
        else:
            r.status = "added"
        filtered.append(r)

    new_results = [r for r in filtered if r.status == "added"]

    # Persist
    _append_history(filtered)
    if new_results:
        _append_pipeline(new_results)

    return ScanSummary(
        total_found=len(unique_results),
        filtered=len([r for r in filtered if r.status != "skipped_dup"]),
        new_added=len(new_results),
        results=new_results,
    )


def print_scan_summary(summary: ScanSummary) -> None:
    console.print(f"\n[bold]Portal Scan Complete[/bold]")
    console.print(f"  Found: {summary.total_found} total")
    console.print(f"  After title filter: {summary.filtered}")
    console.print(f"  New (added to pipeline): {summary.new_added}\n")

    if summary.results:
        table = Table(title="New Offers Added to Pipeline")
        table.add_column("Company")
        table.add_column("Role")
        table.add_column("Via")
        for r in summary.results:
            table.add_row(r.company, r.title, r.discovered_via)
        console.print(table)
        console.print("\nRun [bold]resume-tailor pipeline[/bold] to see pending offers.")
    else:
        console.print("[dim]No new offers found this scan.[/dim]")


def load_pipeline() -> list[dict]:
    """Return pending jobs from pipeline.md."""
    if not PIPELINE_PATH.exists():
        return []
    jobs = []
    for line in PIPELINE_PATH.read_text().splitlines():
        if line.startswith("- [ ] "):
            parts = line[6:].split("|")
            url = parts[0].strip() if parts else ""
            company = parts[1].strip() if len(parts) > 1 else ""
            role = parts[2].strip() if len(parts) > 2 else ""
            if url:
                jobs.append({"url": url, "company": company, "role": role})
    return jobs


def mark_pipeline_processed(url: str, score: Optional[float] = None) -> None:
    """Mark a pipeline URL as processed."""
    if not PIPELINE_PATH.exists():
        return
    content = PIPELINE_PATH.read_text()
    score_str = f", score: {score:.1f}" if score else ""
    today = date.today().isoformat()
    content = content.replace(
        f"- [ ] {url}",
        f"- [x] {url} ({today}{score_str})",
    )
    PIPELINE_PATH.write_text(content)

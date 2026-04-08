"""LinkedIn data: multi-strategy scrape + 7-day cache + Claude parse."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from anthropic import Anthropic
from bs4 import BeautifulSoup
from rich.console import Console

from .models import LinkedInData

console = Console()

_CACHE_PATH = Path(__file__).parent.parent / "data" / "linkedin_data.json"
_CACHE_TTL_HOURS = 7 * 24  # 7 days — LinkedIn profiles don't change often

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache(username: str) -> Optional[LinkedInData]:
    if not _CACHE_PATH.exists():
        return None
    try:
        data = LinkedInData.model_validate_json(_CACHE_PATH.read_text())
        if not data.fetched_at:
            return None
        age_hours = (datetime.now(timezone.utc) - data.fetched_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if age_hours < _CACHE_TTL_HOURS:
            return data
    except Exception:
        pass
    return None


def _save_cache(data: LinkedInData) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(data.model_dump_json(indent=2))


# ── Scraping strategies ───────────────────────────────────────────────────────

def _scrape_playwright(username: str) -> Optional[str]:
    """Direct headless browser scrape."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None

    console.print("[dim]Trying direct LinkedIn scrape...[/dim]")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent=_HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        ).new_page()
        try:
            page.goto(f"https://www.linkedin.com/in/{username}", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_load_state("networkidle", timeout=8000)
            url = page.url
            content = page.content()
            if "authwall" in url or "login" in url or "join linkedin" in content.lower() or len(content) < 2000:
                return None
            text = page.evaluate("() => document.body.innerText")
            return text[:6000] if text and len(text) > 500 else None
        except (PWTimeout, Exception):
            return None
        finally:
            browser.close()


def _scrape_bing_cache(username: str) -> Optional[str]:
    """Try Bing's cached version — less aggressively blocked than Google."""
    url = f"https://cc.bingj.com/cache.aspx?q=linkedin+{username}&url=https://www.linkedin.com/in/{username}"
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(separator="\n", strip=True)
        if len(text) < 500 or "join linkedin" in text.lower() or "bing" not in resp.url.host:
            return None
        return text[:6000]
    except Exception:
        return None


def _scrape_wayback(username: str) -> Optional[str]:
    """Try the Wayback Machine for the most recent archived snapshot."""
    try:
        # Ask Wayback Machine for the latest available snapshot
        avail = httpx.get(
            f"https://archive.org/wayback/available?url=linkedin.com/in/{username}",
            headers=_HEADERS, timeout=10,
        ).json()
        snapshot_url = avail.get("archived_snapshots", {}).get("closest", {}).get("url")
        if not snapshot_url:
            return None
        resp = httpx.get(snapshot_url, headers=_HEADERS, follow_redirects=True, timeout=20)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove Wayback toolbar
        for el in soup.select("#wm-ipp-base, #donato"):
            el.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if len(text) < 500 or "join linkedin" in text.lower():
            return None
        return text[:6000]
    except Exception:
        return None


def _scrape_google_search_snippet(username: str) -> Optional[str]:
    """Scrape the Google search result snippet for the LinkedIn profile page.
    Not the cache — just the description Google shows in results."""
    try:
        resp = httpx.get(
            "https://www.google.com/search",
            params={"q": f"site:linkedin.com/in/{username}", "num": "3", "hl": "en"},
            headers=_HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        # Extract result snippets
        snippets = []
        for el in soup.select("div.VwiC3b, span.st, div[data-sncf]"):
            t = el.get_text(separator=" ", strip=True)
            if t:
                snippets.append(t)
        text = "\n".join(snippets)
        return text[:3000] if len(text) > 100 else None
    except Exception:
        return None


# ── Parse ─────────────────────────────────────────────────────────────────────

def _parse_with_claude(raw_text: str, username: str, client: Anthropic) -> LinkedInData:
    from .claude_engine import _strip_fence, _claude_create
    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Parse this LinkedIn profile content for '{username}' into structured JSON.
Return ONLY valid JSON:
{{
  "headline": "string or null",
  "summary": "string or null",
  "work_history": [
    {{"company": "string", "title": "string", "start_date": "string or null", "end_date": "string or null", "bullets": ["string"]}}
  ],
  "skills": ["string"],
  "education": [
    {{"institution": "string", "degree": "string or null", "field": "string or null", "year": "string or null"}}
  ]
}}

Content:
{raw_text}""",
        }],
    )
    data = json.loads(_strip_fence(response.content[0].text))
    data["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return LinkedInData.model_validate(data)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_linkedin_data(
    username: Optional[str] = None,
    client: Optional[Anthropic] = None,
) -> LinkedInData:
    if not username:
        console.print("[yellow]No LinkedIn username configured — skipping.[/yellow]")
        return LinkedInData(fetched_at=datetime.now(timezone.utc))

    # Return 7-day cache if fresh
    cached = _load_cache(username)
    if cached:
        console.print("[dim]Using cached LinkedIn data (< 7 days old)[/dim]")
        return cached

    if client is None:
        from anthropic import Anthropic as _Anthropic
        from .config import get_anthropic_api_key
        client = _Anthropic(api_key=get_anthropic_api_key())

    strategies = [
        ("direct browser",      _scrape_playwright),
        ("Bing cache",          _scrape_bing_cache),
        ("Wayback Machine",     _scrape_wayback),
        ("Google snippet",      _scrape_google_search_snippet),
    ]

    for label, scrape_fn in strategies:
        console.print(f"[blue]LinkedIn: trying {label}...[/blue]")
        raw = scrape_fn(username)
        if raw:
            console.print(f"[green]LinkedIn data obtained via {label}. Parsing...[/green]")
            data = _parse_with_claude(raw, username, client)
            _save_cache(data)
            return data

    console.print("[yellow]All LinkedIn scraping strategies failed. Proceeding without LinkedIn data.[/yellow]")
    return LinkedInData(fetched_at=datetime.now(timezone.utc))

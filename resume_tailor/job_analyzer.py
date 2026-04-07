"""Ingest job description from text/URL/file and clean it."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from anthropic import Anthropic

import httpx
from bs4 import BeautifulSoup


def _fetch_with_playwright(url: str) -> Optional[str]:
    """Fetch JS-rendered page text via headless browser."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US",
            ).new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                pass
            text = page.evaluate("() => document.body.innerText")
            browser.close()
            return text if text and len(text) > 300 else None
    except Exception:
        return None


def fetch_from_url(url: str) -> str:
    """Fetch job description text from a URL. Falls back to Playwright for JS-rendered pages."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove noisy tags
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Try common job description containers
    for selector in [
        '[class*="job-description"]',
        '[class*="jobDescription"]',
        '[class*="description"]',
        "main",
        "article",
        ".content",
        "#content",
    ]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 300:
                return _clean_text(text)

    static_text = _clean_text(soup.get_text(separator="\n", strip=True))

    # If static scrape is thin, try Playwright for JS-rendered pages
    if len(static_text) < 500:
        pw_text = _fetch_with_playwright(url)
        if pw_text and len(pw_text) > len(static_text):
            return _clean_text(pw_text)

    return static_text


def fetch_from_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Job description file not found: {path}")
    return _clean_text(p.read_text())


def _clean_text(text: str) -> str:
    """Remove excessive blank lines and normalize whitespace."""
    lines = text.splitlines()
    cleaned: list[str] = []
    blank_streak = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_streak += 1
            if blank_streak <= 1:
                cleaned.append("")
        else:
            blank_streak = 0
            cleaned.append(stripped)
    return "\n".join(cleaned).strip()


def ingest_job_description(
    text: Optional[str] = None,
    url: Optional[str] = None,
    file: Optional[str] = None,
) -> str:
    """Return cleaned job description text from one of the three sources."""
    if text:
        return _clean_text(text)
    if url:
        return fetch_from_url(url)
    if file:
        return fetch_from_file(file)
    raise ValueError("Must provide one of: text, url, or file")


def extract_questions(jd_text: str, client: "Anthropic") -> list[str]:
    """Use Claude to extract application questions from job page text.
    Returns an empty list if no questions are found."""
    from .claude_engine import _strip_fence, _claude_create
    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Look at this job posting text and extract any explicit application questions
that a candidate would need to answer when applying (e.g. "Why do you want to work here?",
"Describe a time you...", "What is your experience with...").

Do NOT include:
- Requirements or qualifications listed in the JD
- Generic boilerplate like "equal opportunity employer"
- Job responsibilities

Return a JSON array of question strings. Return [] if there are no questions.
Return ONLY the JSON array.

Job posting text:
{jd_text[:6000]}""",
        }],
    )
    try:
        questions = json.loads(_strip_fence(response.content[0].text))
        return [q for q in questions if isinstance(q, str) and q.strip()]
    except Exception:
        return []

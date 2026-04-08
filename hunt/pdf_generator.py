"""HTML-based PDF generation via Playwright — produces designer-quality resumes."""
from __future__ import annotations

import html
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from rich.console import Console

from .models import TailoredResume

console = Console()

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "cv-template.html"

# Cache template content at module load — safe since the file doesn't change at runtime
_TEMPLATE: Optional[str] = None

def _template() -> str:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")
    return _TEMPLATE

_US_SIGNALS = re.compile(
    r"\b(US|USA|United States|New York|San Francisco|Seattle|Austin|Boston|Chicago|Remote — US)\b",
    re.IGNORECASE,
)


def _detect_page_format(jd_text: str) -> str:
    return "letter" if _US_SIGNALS.search(jd_text) else "A4"


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _build_contact_items(contact: Optional[str]) -> str:
    if not contact:
        return ""
    parts = [p.strip() for p in re.split(r"[|·•,]", contact) if p.strip()]
    items = []
    for part in parts:
        if re.match(r"https?://", part):
            items.append(f'<span><a href="{_esc(part)}">{_esc(part)}</a></span>')
        elif "@" in part:
            items.append(f'<span><a href="mailto:{_esc(part)}">{_esc(part)}</a></span>')
        else:
            items.append(f"<span>{_esc(part)}</span>")
    return "\n    ".join(items)


def _build_competency_tags(keywords: list[str], max_tags: int = 8) -> str:
    tags = keywords[:max_tags]
    return "".join(f'<span class="competency-tag">{_esc(t)}</span>' for t in tags)


def _build_experience_html(experience: list) -> str:
    blocks = []
    for exp in experience:
        bullets_html = "\n".join(
            f"<li>{_esc(b)}</li>" for b in (exp.bullets or [])
        )
        company_part = f'<span class="job-company">· {_esc(exp.company)}</span>' if exp.company else ""
        dates_part = f'<span class="job-dates">{_esc(exp.dates)}</span>' if exp.dates else ""
        blocks.append(f"""<div class="job">
  <div class="job-header">
    <div>
      <span class="job-title">{_esc(exp.title)}</span>{company_part}
    </div>
    {dates_part}
  </div>
  <ul class="job-bullets">{bullets_html}</ul>
</div>""")
    return "\n".join(blocks)


def _build_projects_html(projects: list) -> str:
    if not projects:
        return ""
    blocks = []
    for proj in projects:
        stack_str = ", ".join(proj.tech_stack[:5]) if proj.tech_stack else ""
        stack_part = f'<span class="project-stack">({_esc(stack_str)})</span>' if stack_str else ""
        desc_part = f'<div class="project-desc">{_esc(proj.description)}</div>' if proj.description else ""
        bullets_html = "\n".join(
            f"<li>{_esc(b)}</li>" for b in (proj.bullets or [])
        )
        bullets_part = f'<ul class="job-bullets">{bullets_html}</ul>' if proj.bullets else ""
        blocks.append(f"""<div class="project">
  <div class="project-header">
    <span class="project-name">{_esc(proj.name)}</span>{stack_part}
  </div>
  {desc_part}
  {bullets_part}
</div>""")
    return "\n".join(blocks)


def _build_education_html(education: list) -> str:
    blocks = []
    for edu in education:
        degree_field = " ".join(filter(None, [edu.degree, edu.field]))
        blocks.append(f"""<div class="edu-item">
  <div>
    <span class="edu-institution">{_esc(edu.institution)}</span>
    {f'<span class="edu-degree"> · {_esc(degree_field)}</span>' if degree_field else ""}
  </div>
  {f'<span class="edu-year">{_esc(edu.year)}</span>' if edu.year else ""}
</div>""")
    return "\n".join(blocks)


def generate_pdf(
    tailored: TailoredResume,
    jd_text: str = "",
    jd_keywords: Optional[list[str]] = None,
    output_path: Optional[Path] = None,
    accent_color: str = "#0ea5e9",
) -> Optional[Path]:
    """Render a tailored resume to PDF via Playwright.

    Returns the output path on success, None if Playwright fails.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        console.print("[yellow]Playwright not installed — skipping HTML PDF generation.[/yellow]")
        return None

    page_format = _detect_page_format(jd_text)
    lang = "en"

    # Keywords for competency tags: jd_keywords first, then top skills
    keywords = list(jd_keywords or [])
    for skill in tailored.skills:
        if skill not in keywords:
            keywords.append(skill)

    competency_tags = _build_competency_tags(keywords[:8])

    projects_html = _build_projects_html(tailored.projects or [])
    projects_section = ""
    if projects_html:
        projects_section = f"""<div class="section">
  <div class="section-title">Projects</div>
  {projects_html}
</div>"""

    replacements = {
        "{{LANG}}": lang,
        "{{PAGE_FORMAT}}": page_format,
        "{{ACCENT_COLOR}}": accent_color,
        "{{NAME}}": _esc(tailored.name or ""),
        "{{CONTACT_ITEMS}}": _build_contact_items(tailored.contact),
        "{{SECTION_SUMMARY}}": "Professional Summary",
        "{{SUMMARY_TEXT}}": _esc(tailored.summary or ""),
        "{{SECTION_COMPETENCIES}}": "Core Competencies",
        "{{COMPETENCY_TAGS}}": competency_tags,
        "{{SECTION_EXPERIENCE}}": "Work Experience",
        "{{EXPERIENCE_HTML}}": _build_experience_html(tailored.experience or []),
        "{{PROJECTS_SECTION}}": projects_section,
        "{{SECTION_EDUCATION}}": "Education",
        "{{EDUCATION_HTML}}": _build_education_html(tailored.education or []),
        "{{SECTION_SKILLS}}": "Skills",
        "{{SKILLS_LINE}}": _esc(", ".join(tailored.skills or [])),
    }

    rendered = _template()
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    # Write temp HTML
    tmp_html = Path(tempfile.gettempdir()) / f"hunt_{os.getpid()}.html"
    tmp_html.write_text(rendered, encoding="utf-8")

    if output_path is None:
        output_path = tmp_html.with_suffix(".pdf")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{tmp_html}", wait_until="networkidle", timeout=20000)
            page.pdf(
                path=str(output_path),
                format=page_format,
                margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
                print_background=True,
            )
            browser.close()
        console.print(f"[green]PDF generated:[/green] {output_path}")
        return output_path
    except Exception as e:
        console.print(f"[yellow]HTML PDF generation failed: {e}[/yellow]")
        return None
    finally:
        tmp_html.unlink(missing_ok=True)

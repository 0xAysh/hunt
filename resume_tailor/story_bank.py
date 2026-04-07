"""Persistent interview story bank — accumulates STAR+R stories across all runs."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from rich.console import Console

from .models import InterviewStory, QuestionAnswer

if TYPE_CHECKING:
    from anthropic import Anthropic

console = Console()

STORY_BANK_PATH = Path(__file__).parent.parent / "data" / "story_bank.json"


# ── Persistence ───────────────────────────────────────────────────────────────

def load_story_bank() -> list[InterviewStory]:
    if not STORY_BANK_PATH.exists():
        return []
    try:
        raw = json.loads(STORY_BANK_PATH.read_text())
        return [InterviewStory.model_validate(s) for s in raw]
    except Exception:
        return []


def save_story_bank(stories: list[InterviewStory]) -> None:
    STORY_BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = [s.model_dump(mode="json") for s in stories]
    STORY_BANK_PATH.write_text(json.dumps(data, indent=2, default=str))


# ── Slug helpers ──────────────────────────────────────────────────────────────

def _make_story_id(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def _stories_are_similar(a: InterviewStory, b: InterviewStory) -> bool:
    """Cheap heuristic before calling Claude — same theme + overlapping title words."""
    if a.theme != b.theme:
        return False
    words_a = set(a.title.lower().split())
    words_b = set(b.title.lower().split())
    overlap = words_a & words_b - {"a", "the", "and", "or", "to", "for", "in", "at"}
    return len(overlap) >= 2


# ── Core operations ───────────────────────────────────────────────────────────

def add_stories(
    new_stories: list[InterviewStory],
    client: Optional["Anthropic"] = None,
) -> tuple[int, int]:
    """Merge new stories into the bank. Returns (added, updated)."""
    bank = load_story_bank()
    added = updated = 0

    for new in new_stories:
        # Find potential duplicates by heuristic
        candidates = [s for s in bank if _stories_are_similar(s, new)]

        if not candidates:
            new.id = _make_story_id(new.title)
            bank.append(new)
            added += 1
            continue

        # Take the first candidate as the match (skip Claude call to save tokens)
        existing = candidates[0]
        # Merge: update source_companies, bump times_used, prefer longer content
        merged_companies = list(set(existing.source_companies + new.source_companies))
        merged_requirements = list(set(existing.jd_requirements_matched + new.jd_requirements_matched))

        # Use whichever version has richer content
        if len(new.action) > len(existing.action):
            existing.situation = new.situation
            existing.task = new.task
            existing.action = new.action
            existing.result = new.result
            existing.reflection = new.reflection

        existing.source_companies = merged_companies
        existing.jd_requirements_matched = merged_requirements
        existing.times_used += 1
        existing.updated_at = datetime.now(timezone.utc)
        updated += 1

    save_story_bank(bank)
    return added, updated


def parse_answers_into_stories(
    answers: list[QuestionAnswer],
    company: str,
    jd_requirements: list[str],
) -> list[InterviewStory]:
    """Convert QA pairs (which contain STAR structure) into InterviewStory objects.

    This is a best-effort parse — answers may not always have clean STAR structure.
    """
    stories: list[InterviewStory] = []

    _THEME_KEYWORDS: dict[str, list[str]] = {
        "leadership": ["led", "managed", "mentored", "drove", "organized", "directed"],
        "technical": ["built", "implemented", "designed", "architected", "engineered", "developed"],
        "impact": ["improved", "increased", "reduced", "saved", "grew", "doubled", "optimized"],
        "failure": ["failed", "mistake", "wrong", "learned", "retrospective", "setback"],
        "conflict": ["disagreed", "conflict", "tension", "pushback", "persuaded", "negotiated"],
        "collaboration": ["collaborated", "coordinated", "partnered", "cross-functional", "aligned"],
    }

    def _detect_theme(text: str) -> str:
        lower = text.lower()
        for theme, keywords in _THEME_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                return theme
        return "impact"

    for qa in answers:
        answer_lower = qa.answer.lower()
        # Skip very short answers — not a STAR story
        if len(qa.answer) < 150:
            continue

        theme = _detect_theme(qa.answer)

        # Attempt to split STAR sections — best-effort
        # Many answers will just land in the "action" field which is fine
        story = InterviewStory(
            id=_make_story_id(qa.question[:50]),
            title=qa.question[:80],
            theme=theme,
            situation="See full answer.",
            task="See full answer.",
            action=qa.answer,
            result="(Embedded in answer above.)",
            reflection="(Add reflection after interview practice.)",
            source_companies=[company],
            jd_requirements_matched=[
                req for req in jd_requirements
                if any(word in answer_lower for word in req.lower().split()[:3])
            ][:3],
        )
        stories.append(story)

    return stories


def find_relevant_stories(
    jd_requirements: list[str],
    client: "Anthropic",
    max_stories: int = 6,
) -> list[InterviewStory]:
    """Return stories most relevant to the given JD requirements."""
    bank = load_story_bank()
    if not bank:
        return []

    from .claude_engine import _claude_create, _strip_fence

    bank_summary = "\n".join(
        f"{i}. [{s.theme}] {s.title} — {s.action[:120]}..."
        for i, s in enumerate(bank)
    )

    response = _claude_create(
        client,
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": f"""Given these JD requirements:
{chr(10).join(f'- {r}' for r in jd_requirements[:10])}

And these interview stories (numbered):
{bank_summary}

Return a JSON array of the story numbers (0-indexed) that best match the requirements.
Max {max_stories} stories. Return ONLY the JSON array, e.g. [0, 3, 5].""",
        }],
    )

    try:
        indices = json.loads(_strip_fence(response.content[0].text))
        return [bank[i] for i in indices if 0 <= i < len(bank)]
    except Exception:
        return bank[:max_stories]


# ── Export ────────────────────────────────────────────────────────────────────

def export_story_bank_markdown() -> str:
    bank = load_story_bank()
    if not bank:
        return "# Interview Story Bank\n\n_No stories yet. Stories accumulate as you run resume-tailor._\n"

    by_theme: dict[str, list[InterviewStory]] = {}
    for story in bank:
        by_theme.setdefault(story.theme, []).append(story)

    lines = ["# Interview Story Bank\n"]
    for theme, stories in sorted(by_theme.items()):
        lines.append(f"## {theme.title()}\n")
        for s in stories:
            used = f"Used {s.times_used}x" if s.times_used else "Not yet used"
            companies = ", ".join(s.source_companies[:3]) if s.source_companies else "manual"
            lines.append(f"### {s.title}")
            lines.append(f"_{used} · From: {companies}_\n")
            lines.append(f"**Situation:** {s.situation}\n")
            lines.append(f"**Task:** {s.task}\n")
            lines.append(f"**Action:** {s.action}\n")
            lines.append(f"**Result:** {s.result}\n")
            lines.append(f"**Reflection:** {s.reflection}\n")
            if s.jd_requirements_matched:
                lines.append(f"_Answers: {', '.join(s.jd_requirements_matched)}_\n")
            lines.append("---\n")

    return "\n".join(lines)

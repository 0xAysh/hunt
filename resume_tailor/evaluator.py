"""Offer evaluation and scoring — runs before resume tailoring."""
from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from rich.console import Console

from .models import AppConfig, OfferEvaluation, OfferGap, OfferScore

if TYPE_CHECKING:
    from anthropic import Anthropic

console = Console()

_EVAL_SYSTEM = """You are a rigorous career advisor. Your job is to evaluate job offers objectively
and help candidates avoid wasting time on poor-fit roles.

RULES:
- Score each dimension 1-5 based on evidence in the JD and candidate profile
- Be conservative: a score of 4+ means this role is genuinely strong
- Never inflate scores to make the candidate feel good
- Identify real gaps, not hypothetical ones
- Recommendations: "apply" (>= 4.0), "consider" (3.5-3.9), "skip" (< 3.5)
- Output ONLY valid JSON when JSON is requested"""


def evaluate_offer(
    jd_text: str,
    profile_context: str,
    client: "Anthropic",
    config: Optional[AppConfig] = None,
) -> OfferEvaluation:
    """Score a job offer across 5 dimensions and produce a full A-F evaluation.

    Scoring formula:
        global = cv_match*0.35 + growth*0.15 + comp*0.20 + culture*0.15 + clarity*0.15 - red_flags
    """
    from .claude_engine import _claude_create, _strip_fence

    console.print("[blue]Evaluating offer fit...[/blue]")

    user_prefs = ""
    if config:
        if config.target_roles:
            user_prefs += f"\nTarget roles: {', '.join(config.target_roles)}"
        if config.salary_range:
            user_prefs += f"\nSalary target: {config.salary_range}"
        if config.remote_preference:
            user_prefs += f"\nRemote preference: {config.remote_preference}"
        if config.deal_breakers:
            user_prefs += f"\nDeal-breakers: {', '.join(config.deal_breakers)}"

    prompt = f"""Evaluate this job offer. Return a single JSON object.

CANDIDATE PROFILE:
{profile_context}
{user_prefs}

JOB DESCRIPTION:
{jd_text[:8000]}

Return this exact JSON structure (no markdown fences):
{{
  "role_summary": "1-2 sentence TL;DR of the role",
  "cv_match_analysis": "Detailed requirement-by-requirement analysis. Quote exact JD lines. Be specific.",
  "gaps": [
    {{"skill": "skill name", "is_blocker": true/false, "mitigation": "how to address"}}
  ],
  "level_strategy": "Seniority positioning advice — how to frame the candidate's experience for this level",
  "comp_research": "Salary range estimate for this role/location based on your training data. Note if uncertain.",
  "personalization_plan": "Top 5 concrete CV changes: which sections, what to change, why",
  "interview_stories": [
    {{"requirement": "JD requirement", "story_theme": "leadership/technical/impact/etc", "story_hook": "One-line hook for a STAR story"}}
  ],
  "score": {{
    "cv_match": <1-5, weight 0.35>,
    "growth_potential": <1-5, weight 0.15>,
    "compensation": <1-5, weight 0.20>,
    "culture_signals": <1-5, weight 0.15>,
    "role_clarity": <1-5, weight 0.15>,
    "red_flags": <0-2, subtracted from global>,
    "global_score": <computed: cv*0.35 + growth*0.15 + comp*0.20 + culture*0.15 + clarity*0.15 - red_flags, clamp 1-5>,
    "recommendation": "apply" or "consider" or "skip",
    "reasoning": "1-2 sentences on the recommendation"
  }}
}}"""

    response = _claude_create(
        client,
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=_EVAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = _strip_fence(response.content[0].text)
    data = json.loads(raw)

    score_data = data["score"]
    score = OfferScore(
        cv_match=score_data["cv_match"],
        growth_potential=score_data["growth_potential"],
        compensation=score_data["compensation"],
        culture_signals=score_data["culture_signals"],
        role_clarity=score_data["role_clarity"],
        red_flags=score_data.get("red_flags", 0.0),
        global_score=score_data["global_score"],
        recommendation=score_data["recommendation"],
        reasoning=score_data["reasoning"],
    )

    gaps = [OfferGap(**g) for g in data.get("gaps", [])]

    return OfferEvaluation(
        score=score,
        role_summary=data["role_summary"],
        cv_match_analysis=data["cv_match_analysis"],
        gaps=gaps,
        level_strategy=data["level_strategy"],
        comp_research=data["comp_research"],
        personalization_plan=data["personalization_plan"],
        interview_stories=data.get("interview_stories", []),
    )


def format_evaluation_report(
    evaluation: OfferEvaluation,
    company: Optional[str],
    role: Optional[str],
    url: Optional[str],
) -> str:
    """Render evaluation as a markdown report."""
    score = evaluation.score
    date = __import__("datetime").date.today().isoformat()
    rec_emoji = {"apply": "✅", "consider": "🟡", "skip": "❌"}.get(score.recommendation, "")

    gaps_md = ""
    if evaluation.gaps:
        gaps_md = "\n".join(
            f"- **{g.skill}** {'🚫 blocker' if g.is_blocker else '⚠️ gap'}: {g.mitigation}"
            for g in evaluation.gaps
        )
    else:
        gaps_md = "None — strong coverage."

    stories_md = ""
    if evaluation.interview_stories:
        rows = "\n".join(
            f"| {s.get('requirement', '')} | {s.get('story_theme', '')} | {s.get('story_hook', '')} |"
            for s in evaluation.interview_stories
        )
        stories_md = f"| JD Requirement | Theme | Story Hook |\n|---|---|---|\n{rows}"
    else:
        stories_md = "_No stories mapped._"

    return f"""# Evaluation: {company or 'Unknown'} — {role or 'Unknown'}

**Date:** {date}
**URL:** {url or 'N/A'}
**Score:** {score.global_score:.1f}/5 {rec_emoji}
**Recommendation:** {score.recommendation.upper()}

> {score.reasoning}

---

## Score Breakdown

| Dimension | Score | Weight |
|-----------|-------|--------|
| CV Match | {score.cv_match}/5 | 35% |
| Growth Potential | {score.growth_potential}/5 | 15% |
| Compensation | {score.compensation}/5 | 20% |
| Culture Signals | {score.culture_signals}/5 | 15% |
| Role Clarity | {score.role_clarity}/5 | 15% |
| Red Flags | -{score.red_flags} | deducted |
| **Global** | **{score.global_score:.1f}/5** | |

---

## A) Role Summary

{evaluation.role_summary}

## B) CV Match Analysis

{evaluation.cv_match_analysis}

### Gaps

{gaps_md}

## C) Level & Strategy

{evaluation.level_strategy}

## D) Compensation Research

{evaluation.comp_research}

## E) Personalization Plan

{evaluation.personalization_plan}

## F) Interview Stories

{stories_md}
"""

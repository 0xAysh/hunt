"""Compensation research and salary negotiation scripts."""
from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from rich.console import Console

from .models import CompResearch, NegotiationScript

if TYPE_CHECKING:
    from anthropic import Anthropic

console = Console()

_NEGOTIATION_SYSTEM = """You are a salary negotiation expert with deep knowledge of tech compensation.
Your advice is direct, evidence-based, and never wishy-washy.
Scripts should sound confident but natural — not corporate, not aggressive.
Output ONLY valid JSON when asked."""

# Five core negotiation scenarios always generated
_SCENARIOS = [
    (
        "initial_offer",
        "You just received a verbal or written offer and haven't responded yet.",
    ),
    (
        "counter_offer",
        "You want to counter the initial offer with a higher number.",
    ),
    (
        "geographic_discount",
        "The company is trying to pay you less because you live in a cheaper city.",
    ),
    (
        "competing_offer",
        "You have another offer (real or expected) to use as leverage.",
    ),
    (
        "equity_negotiation",
        "Base is capped but you want to negotiate equity or signing bonus.",
    ),
]


def research_compensation(
    role: str,
    company: Optional[str],
    location: Optional[str],
    client: "Anthropic",
) -> CompResearch:
    """Estimate market compensation for a role using Claude's training data."""
    from .claude_engine import _claude_create, _strip_fence

    console.print("[blue]Researching market compensation...[/blue]")

    location_str = location or "US (general)"
    company_str = company or "typical tech company"

    prompt = f"""Research market compensation for this role. Use your training data — be honest when uncertain.

Role: {role}
Company: {company_str}
Location: {location_str}

Return JSON:
{{
  "role_title": "{role}",
  "company": "{company_str}",
  "salary_range_low": <integer, USD annual>,
  "salary_range_mid": <integer, USD annual>,
  "salary_range_high": <integer, USD annual>,
  "currency": "USD",
  "equity_notes": "typical equity range or null",
  "sources": ["Levels.fyi", "Glassdoor", "Blind", etc — sources you drew from],
  "location_adjustment": "adjustment note if location affects pay",
  "notes": "any caveats, uncertainty, or additional context"
}}

Return ONLY the JSON object."""

    response = _claude_create(
        client,
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=_NEGOTIATION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    data = json.loads(_strip_fence(response.content[0].text))
    return CompResearch.model_validate(data)


def generate_negotiation_scripts(
    comp_research: CompResearch,
    user_salary_target: Optional[str],
    candidate_name: Optional[str],
    profile_summary: Optional[str],
    client: "Anthropic",
) -> list[NegotiationScript]:
    """Generate 5 negotiation scripts personalized to the candidate and role."""
    from .claude_engine import _claude_create, _strip_fence

    console.print("[blue]Generating negotiation scripts...[/blue]")

    market_data = (
        f"P25: ${comp_research.salary_range_low:,}\n"
        f"P50: ${comp_research.salary_range_mid:,}\n"
        f"P75: ${comp_research.salary_range_high:,}"
        if comp_research.salary_range_low and comp_research.salary_range_mid
        else "Market data: see comp_research notes"
    )

    scenarios_list = "\n".join(
        f'{i+1}. scenario: "{s}", context: "{c}"' for i, (s, c) in enumerate(_SCENARIOS)
    )

    prompt = f"""Generate 5 salary negotiation scripts. Each must be specific and ready to use.

ROLE: {comp_research.role_title} at {comp_research.company}
CANDIDATE: {candidate_name or 'the candidate'}
MARKET DATA:
{market_data}
TARGET SALARY: {user_salary_target or 'not specified — aim for P75'}
PROFILE: {profile_summary or 'strong technical candidate'}

SCENARIOS:
{scenarios_list}

Return JSON array — one object per scenario:
[
  {{
    "scenario": "<scenario key from above>",
    "context": "<when to use this>",
    "script": "<the actual words — ready to say or send>",
    "key_principles": ["principle 1", "principle 2"]
  }}
]

Rules for scripts:
- Sound like a real person, not a template
- Reference the specific role and company
- Use the market data as anchor when relevant
- Include silence tactics for verbal negotiations
- Never sound desperate or apologetic
- "I" voice — written from the candidate's perspective

Return ONLY the JSON array."""

    response = _claude_create(
        client,
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system=_NEGOTIATION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    data = json.loads(_strip_fence(response.content[0].text))
    return [NegotiationScript.model_validate(s) for s in data]


def format_negotiation_report(
    comp_research: CompResearch,
    scripts: list[NegotiationScript],
    company: Optional[str],
    role: Optional[str],
) -> str:
    """Render comp research + scripts as a markdown document."""
    lines = [
        f"# Negotiation Playbook: {company or 'Company'} — {role or 'Role'}\n",
        "## Market Compensation Data\n",
        "| Metric | Value |",
        "|--------|-------|",
    ]

    if comp_research.salary_range_low:
        lines.append(f"| P25 | ${comp_research.salary_range_low:,} |")
    if comp_research.salary_range_mid:
        lines.append(f"| P50 (median) | ${comp_research.salary_range_mid:,} |")
    if comp_research.salary_range_high:
        lines.append(f"| P75 | ${comp_research.salary_range_high:,} |")
    if comp_research.equity_notes:
        lines.append(f"| Equity | {comp_research.equity_notes} |")
    if comp_research.location_adjustment:
        lines.append(f"| Location adjustment | {comp_research.location_adjustment} |")

    if comp_research.sources:
        lines.append(f"\n_Sources: {', '.join(comp_research.sources)}_")
    if comp_research.notes:
        lines.append(f"\n> {comp_research.notes}")

    lines.append("\n---\n")

    for i, script in enumerate(scripts, 1):
        scenario_label = script.scenario.replace("_", " ").title()
        lines.append(f"## Script {i}: {scenario_label}\n")
        lines.append(f"**When to use:** {script.context}\n")
        lines.append(f"---\n\n{script.script}\n\n---\n")
        if script.key_principles:
            lines.append("**Principles applied:**")
            for p in script.key_principles:
                lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)

"""All Claude API calls and 7-step resume tailoring pipeline."""
from __future__ import annotations

import json
import time
from typing import Optional

from anthropic import Anthropic, APIStatusError
from rich.console import Console

from .models import (
    GitHubProfile,
    JobAnalysis,
    LinkedInData,
    QuestionAnswer,
    ResumeDocument,
    TailoredResume,
)

console = Console()

SYSTEM_PROMPT = """You are an expert resume writer and ATS optimization specialist.
Your goal is to tailor resumes to maximize both ATS keyword matching (65-75% target)
and human recruiter engagement.

STRICT RULES:
1. NEVER fabricate experience, skills, metrics, or achievements not present in the source data
2. NEVER invent numbers — only use metrics found in the provided profile or extrapolate with clear proxies
3. Always mirror the exact verb forms and terminology used in the job description
4. Quantify bullets where possible using real data; if no metric exists, use a scope proxy (e.g., "across 3 microservices")
5. Reorder bullets within each role so the most JD-relevant appear first (F-pattern reading)
6. Include both acronym and full form for technical skills (e.g., "APIs (Application Programming Interfaces)")
7. Output ONLY valid JSON when JSON is requested — no markdown fences, no explanation
8. ONE PAGE ONLY: max 3 bullets per role, max 4 roles, max 3 projects, summary ≤ 2 sentences, skills as one comma-separated line. Cut ruthlessly — quality over quantity."""


def _claude_create(client: Anthropic, **kwargs) -> object:
    """Wrapper around client.messages.create with retry on 529/529 overloaded errors."""
    max_retries = 5
    base_delay = 10
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except APIStatusError as e:
            if e.status_code in (529, 529) or getattr(e, "status_code", None) == 529:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    console.print(f"[yellow]API overloaded (529). Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})[/yellow]")
                    time.sleep(delay)
                    continue
            raise
    raise RuntimeError("Max retries exceeded for Claude API call")


def _strip_fence(text: str) -> str:
    """Strip markdown code fences from a Claude response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text.strip()


def _build_profile_context(
    resume: ResumeDocument,
    github: Optional[GitHubProfile],
    linkedin: Optional[LinkedInData],
) -> str:
    """Step 2: Assemble structured candidate context (pure Python, no Claude call)."""
    parts: list[str] = []

    parts.append("=== CANDIDATE RESUME ===")
    if resume.name:
        parts.append(f"Name: {resume.name}")
    if resume.summary:
        parts.append(f"\nSummary:\n{resume.summary}")
    if resume.experience:
        parts.append("\nExperience:")
        for exp in resume.experience:
            parts.append(f"  {exp.title} at {exp.company} ({exp.dates or 'dates unknown'})")
            for b in exp.bullets:
                parts.append(f"    • {b}")
    if resume.skills:
        parts.append(f"\nSkills: {', '.join(resume.skills)}")
    if resume.projects:
        parts.append("\nProjects:")
        for proj in resume.projects:
            parts.append(f"  {proj.name}: {proj.description or ''}")
            for b in proj.bullets:
                parts.append(f"    • {b}")
    if resume.education:
        parts.append("\nEducation:")
        for edu in resume.education:
            parts.append(f"  {edu.degree or ''} {edu.field or ''} — {edu.institution} {edu.year or ''}")

    if github:
        parts.append("\n=== GITHUB PROFILE ===")
        parts.append(f"Username: {github.username}")
        if github.bio:
            parts.append(f"Bio: {github.bio}")
        parts.append(f"Top Languages: {', '.join(github.top_languages[:8])}")
        if github.repos:
            parts.append("Notable Repositories:")
            for repo in sorted(github.repos, key=lambda r: r.stars, reverse=True)[:10]:
                desc = repo.description or "no description"
                langs = ", ".join(repo.languages[:3])
                parts.append(f"  ★{repo.stars} {repo.name} ({langs}): {desc}")
        if github.profile_readme:
            parts.append(f"\nProfile README (excerpt):\n{github.profile_readme[:1000]}")

    if linkedin:
        parts.append("\n=== LINKEDIN PROFILE ===")
        if linkedin.headline:
            parts.append(f"Headline: {linkedin.headline}")
        if linkedin.summary:
            parts.append(f"Summary: {linkedin.summary[:500]}")
        if linkedin.work_history:
            parts.append("Work History:")
            for job in linkedin.work_history:
                parts.append(f"  {job.title} at {job.company} ({job.start_date}–{job.end_date})")
                for b in job.bullets:
                    parts.append(f"    • {b}")
        if linkedin.skills:
            parts.append(f"Skills: {', '.join(linkedin.skills[:30])}")

    return "\n".join(parts)


# ── Step 1: Job Description Analysis ─────────────────────────────────────────

def analyze_job_description(jd_text: str, client: Anthropic) -> JobAnalysis:
    console.print("[blue]Step 1: Analyzing job description...[/blue]")
    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"""Analyze this job description and return a JSON object with these exact keys:
{{
  "required_skills": ["list of must-have technical skills"],
  "preferred_skills": ["list of nice-to-have skills"],
  "tech_stack": ["list of all mentioned technologies"],
  "responsibilities": ["list of key responsibilities, max 10"],
  "seniority": "Junior|Mid-level|Senior|Staff|Principal or null",
  "soft_skills": ["communication", "collaboration", etc.],
  "action_verbs": ["verbs the JD uses: architect, optimize, lead, etc."],
  "company_values": ["values extracted from company description"],
  "keywords_both_forms": [["SQL", "Structured Query Language"], ["API", "Application Programming Interface"]]
}}

Return ONLY the JSON object.

Job Description:
{jd_text}""",
            }
        ],
    )
    raw = _strip_fence(response.content[0].text)
    if not raw:
        console.print("[yellow]Warning: Claude returned empty response for JD analysis. Retrying with simpler prompt...[/yellow]")
        response2 = _claude_create(client,
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"Extract skills and keywords from this job description as JSON with keys: required_skills, preferred_skills, tech_stack, responsibilities, seniority, soft_skills, action_verbs, company_values, keywords_both_forms. Return ONLY JSON.\n\n{jd_text[:4000]}",
            }],
        )
        raw = _strip_fence(response2.content[0].text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"[yellow]Claude response was not valid JSON, using defaults. Response: {raw[:200]}[/yellow]")
        data = {
            "required_skills": [], "preferred_skills": [], "tech_stack": [],
            "responsibilities": [], "seniority": None, "soft_skills": [],
            "action_verbs": [], "company_values": [], "keywords_both_forms": [],
        }
    data["raw_text"] = jd_text
    return JobAnalysis.model_validate(data)


# ── Step 3: Gap Analysis & Tailoring Strategy ─────────────────────────────────

def _step3_gap_analysis(
    profile_context: str,
    job: JobAnalysis,
    client: Anthropic,
) -> tuple[list, str]:  # (messages_history, gap_text)
    console.print("[blue]Step 3: Gap analysis and tailoring strategy...[/blue]")
    messages = [
        {
            "role": "user",
            "content": f"""You are about to tailor a resume for this role.
First, ANALYZE — do not write the resume yet.

JOB ANALYSIS:
Required Skills: {', '.join(job.required_skills)}
Preferred Skills: {', '.join(job.preferred_skills)}
Tech Stack: {', '.join(job.tech_stack)}
Responsibilities: {chr(10).join(f'• {r}' for r in job.responsibilities)}
Seniority: {job.seniority}
Action Verbs Used: {', '.join(job.action_verbs)}
Company Values: {', '.join(job.company_values)}

CANDIDATE PROFILE:
{profile_context}

Provide:
1. PRESENT SKILLS (exact or clearly implied in profile)
2. ABSENT SKILLS — mark as [GAP], do NOT suggest adding them to resume
3. TOP 3 EXPERIENCES/PROJECTS most relevant to this role (and why)
4. WEAK BULLETS (generic, no metrics, vague verbs) vs STRONG BULLETS (quantified, specific)
5. GITHUB PROJECTS to surface in resume Projects section
6. TAILORING STRATEGY — which sections to rewrite, which bullets to reorder, what verb style to adopt

Be specific. Reference actual bullets and project names from the profile.""",
        }
    ]

    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    gap_text = response.content[0].text
    messages.append({"role": "assistant", "content": gap_text})
    return messages, gap_text


# ── Step 4: Resume Rewriting ──────────────────────────────────────────────────

def _step4_rewrite_resume(
    messages: list,
    job: JobAnalysis,
    resume: ResumeDocument,
    client: Anthropic,
) -> TailoredResume:
    console.print("[blue]Step 4: Rewriting resume...[/blue]")

    schema = """{
  "name": "string or null",
  "contact": "string or null",
  "summary": "rewritten summary — lead with 2-3 JD keywords, speak directly to this role",
  "experience": [
    {
      "company": "string",
      "title": "string",
      "dates": "string or null",
      "bullets": ["reordered and reworded bullets — most JD-relevant first"]
    }
  ],
  "skills": ["reordered skills — JD matches first, exact JD keyword forms"],
  "education": [{"institution": "string", "degree": "string", "field": "string", "year": "string"}],
  "projects": [
    {
      "name": "string",
      "description": "string",
      "tech_stack": ["list"],
      "bullets": ["impact bullets"],
      "url": "string or null"
    }
  ],
  "sections_modified": [
    {"section": "string", "change": "what changed", "reason": "why — reference JD evidence"}
  ],
  "keyword_match_score": 0,
  "keywords_added": ["list of JD keywords added to resume"],
  "bullets_quantified": 0
}"""

    messages.append(
        {
            "role": "user",
            "content": f"""Now produce the tailored resume as JSON.

RULES:
- Mirror JD action verbs exactly: {', '.join(job.action_verbs)}
- Reorder bullets within each role: most JD-relevant FIRST (F-pattern)
- Add both acronym + full form for key skills where appropriate
- Rewrite Summary to open with 2-3 JD keywords
- Surface relevant GitHub projects in Projects section
- Skills section: put JD-matching skills first using exact JD keyword forms
- keywords_match_score: estimate 0-100 (target 65-75)
- bullets_quantified: count of bullets with numbers/metrics
- NEVER fabricate data — only use what's in the candidate profile

Return ONLY valid JSON matching this schema:
{schema}

Original resume data for reference:
Name: {resume.name}
Contact: {resume.contact}
Current skills: {', '.join(resume.skills[:20])}
Education: {json.dumps([e.model_dump() for e in resume.education])}""",
        }
    )

    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return TailoredResume.model_validate(json.loads(_strip_fence(response.content[0].text)))


# ── Step 5: ATS Compliance Check ─────────────────────────────────────────────

def check_ats_compliance(tailored: TailoredResume, job: JobAnalysis, client: Anthropic) -> str:
    console.print("[blue]Step 5: ATS compliance check...[/blue]")
    resume_text = f"""
Summary: {tailored.summary}
Skills: {', '.join(tailored.skills)}
Experience bullets:
{chr(10).join(b for exp in tailored.experience for b in exp.bullets)}
Projects:
{chr(10).join(b for proj in tailored.projects for b in proj.bullets)}
"""
    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": f"""Check this tailored resume for ATS compliance issues.
Required keywords to verify: {', '.join(job.required_skills)}

Resume text:
{resume_text}

Report:
1. Which required keywords appear 2-3x (ideal), <2x (too few), or >3x (stuffing risk)?
2. Any keywords that ONLY appear in skills section (OK) vs appearing in context (better)?
3. Are acronym + full forms both present for major skills?
4. Any other ATS red flags?

Be concise — max 200 words.""",
            }
        ],
    )
    return response.content[0].text


# ── Step 6: Application Question Answers ──────────────────────────────────────

def answer_questions(
    questions: list[str],
    tailored: TailoredResume,
    job: JobAnalysis,
    client: Anthropic,
) -> list[QuestionAnswer]:
    if not questions:
        return []
    console.print(f"[blue]Step 6: Writing answers to {len(questions)} question(s)...[/blue]")

    q_list = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    resume_summary = f"""
Name: {tailored.name}
Role being applied for — seniority: {job.seniority}
Summary: {tailored.summary}
Key experiences:
{chr(10).join(f"• {exp.title} at {exp.company}: {exp.bullets[0] if exp.bullets else ''}" for exp in tailored.experience[:4])}
Key projects:
{chr(10).join(f"• {proj.name}: {proj.description or ''}" for proj in tailored.projects[:3])}
Company values: {', '.join(job.company_values)}
"""

    response = _claude_create(client,
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"""Write STAR-format answers (150-300 words each) for these application questions.

RULES:
- STAR format: Situation → Task → Action → Result
- Reference specific projects, companies, and technologies from the candidate's profile
- Align with company values: {', '.join(job.company_values)}
- Mirror vocabulary from the job description
- Include at least one quantified outcome per answer
- NEVER fabricate — only use real experiences from the profile

Candidate Profile Summary:
{resume_summary}

Questions:
{q_list}

Return a JSON array:
[{{"question": "...", "answer": "..."}}]

Return ONLY the JSON array.""",
            }
        ],
    )
    data = json.loads(_strip_fence(response.content[0].text))
    return [QuestionAnswer.model_validate(item) for item in data]


# ── Step 7: Gap Analysis Report ───────────────────────────────────────────────

def generate_gap_report(
    tailored: TailoredResume,
    job: JobAnalysis,
    gap_analysis_text: str,
    ats_report: str,
    client: Anthropic,
) -> str:
    console.print("[blue]Step 7: Generating gap analysis report...[/blue]")

    changes_table = "\n".join(
        f"| {c.section} | {c.change} | {c.reason} |"
        for c in tailored.sections_modified
    )
    gaps = [s for s in job.required_skills if s.lower() not in " ".join(tailored.skills).lower()]
    strengths_context = "\n".join(
        f"• {k}: {v}" for k, v in {
            "Keyword match score": f"{tailored.keyword_match_score}/100",
            "Keywords added": ", ".join(tailored.keywords_added[:10]),
            "Bullets quantified": str(tailored.bullets_quantified),
        }.items()
    )

    target_ok = "✓" if 65 <= tailored.keyword_match_score <= 75 else "⚠"

    return f"""# Gap Analysis Report

## Keyword Match Score: {tailored.keyword_match_score}/100 {target_ok} (target: 65-75)

## What Was Changed

| Section | Change | Reason |
|---------|--------|--------|
{changes_table}

## Skills Gaps (not added — candidate doesn't have them)
{chr(10).join(f"- {g}: NOT in your profile." for g in gaps) or "None — great coverage!"}

## ATS Compliance
{ats_report}

## Strengths for This Role
{strengths_context}

## Detailed Strategy (Claude's Analysis)
{gap_analysis_text[:2000]}
"""


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    resume: ResumeDocument,
    jd_text: str,
    questions: list[str],
    github: Optional[GitHubProfile],
    linkedin: Optional[LinkedInData],
    client: Anthropic,
) -> tuple[TailoredResume, list[QuestionAnswer], str]:
    """Run all 7 steps. Returns (tailored_resume, answers, gap_report_markdown)."""
    job = analyze_job_description(jd_text, client)
    profile_context = _build_profile_context(resume, github, linkedin)
    messages, gap_text = _step3_gap_analysis(profile_context, job, client)
    tailored = _step4_rewrite_resume(messages, job, resume, client)
    ats_report = check_ats_compliance(tailored, job, client)
    answers = answer_questions(questions, tailored, job, client)
    gap_report = generate_gap_report(tailored, job, gap_text, ats_report, client)
    return tailored, answers, gap_report

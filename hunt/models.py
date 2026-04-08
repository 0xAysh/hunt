"""Pydantic models for all shared types."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


# ── GitHub ────────────────────────────────────────────────────────────────────

class GitHubRepo(BaseModel):
    name: str
    description: Optional[str] = None
    url: str
    stars: int = 0
    languages: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    is_pinned: bool = False


class GitHubProfile(BaseModel):
    username: str
    bio: Optional[str] = None
    profile_readme: Optional[str] = None
    repos: list[GitHubRepo] = Field(default_factory=list)
    pinned_repos: list[GitHubRepo] = Field(default_factory=list)
    top_languages: list[str] = Field(default_factory=list)
    fetched_at: Optional[datetime] = None


# ── LinkedIn ──────────────────────────────────────────────────────────────────

class WorkExperience(BaseModel):
    company: str
    title: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    year: Optional[str] = None


class LinkedInData(BaseModel):
    headline: Optional[str] = None
    summary: Optional[str] = None
    work_history: list[WorkExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    fetched_at: Optional[datetime] = None


# ── Job Analysis ──────────────────────────────────────────────────────────────

class JobAnalysis(BaseModel):
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    seniority: Optional[str] = None
    soft_skills: list[str] = Field(default_factory=list)
    action_verbs: list[str] = Field(default_factory=list)
    company_values: list[str] = Field(default_factory=list)
    keywords_both_forms: list[list[str]] = Field(default_factory=list)
    raw_text: str = ""


# ── Resume ────────────────────────────────────────────────────────────────────

class ResumeSection(BaseModel):
    heading: str
    content: str


class ResumeExperience(BaseModel):
    company: str
    title: str
    dates: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)


class ResumeProject(BaseModel):
    name: str
    description: Optional[str] = None
    tech_stack: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    url: Optional[str] = None


class ResumeDocument(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    summary: Optional[str] = None
    experience: list[ResumeExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    raw_sections: list[ResumeSection] = Field(default_factory=list)


class SectionChange(BaseModel):
    section: str
    change: str
    reason: str


class TailoredResume(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    summary: Optional[str] = None
    experience: list[ResumeExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    sections_modified: list[SectionChange] = Field(default_factory=list)
    keyword_match_score: int = 0
    keywords_added: list[str] = Field(default_factory=list)
    bullets_quantified: int = 0


class QuestionAnswer(BaseModel):
    question: str
    answer: str


# ── Offer Evaluation ──────────────────────────────────────────────────────────

class OfferScore(BaseModel):
    cv_match: float = Field(ge=1.0, le=5.0)
    growth_potential: float = Field(ge=1.0, le=5.0)
    compensation: float = Field(ge=1.0, le=5.0)
    culture_signals: float = Field(ge=1.0, le=5.0)
    role_clarity: float = Field(ge=1.0, le=5.0)
    red_flags: float = Field(ge=0.0, le=2.0, default=0.0)
    global_score: float = Field(ge=1.0, le=5.0)
    recommendation: str  # "apply" | "consider" | "skip"
    reasoning: str


class OfferGap(BaseModel):
    skill: str
    is_blocker: bool
    mitigation: str


class OfferEvaluation(BaseModel):
    score: OfferScore
    role_summary: str
    cv_match_analysis: str
    gaps: list[OfferGap] = Field(default_factory=list)
    level_strategy: str
    comp_research: str
    personalization_plan: str
    interview_stories: list[dict] = Field(default_factory=list)


# ── Interview Story Bank ──────────────────────────────────────────────────────

class InterviewStory(BaseModel):
    id: str
    title: str
    theme: str  # "leadership" | "failure" | "conflict" | "technical" | "impact" | "collaboration"
    situation: str
    task: str
    action: str
    result: str
    reflection: str
    source_companies: list[str] = Field(default_factory=list)
    jd_requirements_matched: list[str] = Field(default_factory=list)
    times_used: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Comp & Negotiation ────────────────────────────────────────────────────────

class CompResearch(BaseModel):
    role_title: str
    company: Optional[str] = None
    salary_range_low: Optional[int] = None
    salary_range_mid: Optional[int] = None
    salary_range_high: Optional[int] = None
    currency: str = "USD"
    equity_notes: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    location_adjustment: Optional[str] = None
    notes: Optional[str] = None


class NegotiationScript(BaseModel):
    scenario: str  # "initial_offer" | "counter" | "geographic_discount" | "competing_offer" | "equity"
    context: str
    script: str
    key_principles: list[str] = Field(default_factory=list)


# ── Scanner ───────────────────────────────────────────────────────────────────

class ScanResult(BaseModel):
    title: str
    company: str
    url: str
    platform: Optional[str] = None
    discovered_via: str  # "playwright" | "api" | "websearch"
    scan_date: str
    status: str = "added"  # added | skipped_title | skipped_dup


class ScanSummary(BaseModel):
    total_found: int
    filtered: int
    new_added: int
    results: list[ScanResult] = Field(default_factory=list)


# ── Batch Processing ──────────────────────────────────────────────────────────

class BatchJob(BaseModel):
    id: int
    url: str
    company: Optional[str] = None
    role: Optional[str] = None
    status: str = "pending"  # pending | processing | completed | failed
    score: Optional[float] = None
    recommendation: Optional[str] = None
    error: Optional[str] = None
    run_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class BatchState(BaseModel):
    batch_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    jobs: list[BatchJob] = Field(default_factory=list)
    parallel_workers: int = 3
    min_score_for_tailoring: float = 3.5


# ── Config ────────────────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    github_username: Optional[str] = None
    linkedin_username: Optional[str] = None
    base_resume_path: str = "data/base_resume.docx"
    github_token: Optional[str] = None
    # Evaluation & personalization
    target_roles: list[str] = Field(default_factory=list)
    salary_range: Optional[str] = None
    location: Optional[str] = None
    remote_preference: Optional[str] = None
    deal_breakers: list[str] = Field(default_factory=list)
    preferred_company_stage: Optional[str] = None
    # PDF styling
    pdf_accent_color: str = "#0ea5e9"


# ── Run Metadata ──────────────────────────────────────────────────────────────

class RunMeta(BaseModel):
    run_id: str
    company: Optional[str] = None
    role: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    job_description_source: Optional[str] = None
    keyword_match_score: int = 0
    offer_score: Optional[float] = None
    recommendation: Optional[str] = None
    approved: bool = False
    approved_at: Optional[datetime] = None

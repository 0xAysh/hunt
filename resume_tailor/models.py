"""Pydantic models for all shared types."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


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


class AppConfig(BaseModel):
    github_username: Optional[str] = None
    linkedin_username: Optional[str] = None
    base_resume_path: str = "data/base_resume.docx"
    github_token: Optional[str] = None


class RunMeta(BaseModel):
    run_id: str
    company: Optional[str] = None
    role: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    job_description_source: Optional[str] = None
    keyword_match_score: int = 0
    approved: bool = False
    approved_at: Optional[datetime] = None

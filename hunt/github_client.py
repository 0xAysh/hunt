"""GitHub API client."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from github import Github, GithubException
from rich.console import Console

from .models import GitHubProfile, GitHubRepo

console = Console()


def fetch_github_profile(
    username: str,
    token: Optional[str] = None,
) -> GitHubProfile:
    console.print(f"[blue]Fetching GitHub profile for {username}...[/blue]")
    g = Github(token) if token else Github()

    try:
        user = g.get_user(username)
    except GithubException as e:
        raise RuntimeError(f"Failed to fetch GitHub user '{username}': {e}") from e

    repos: list[GitHubRepo] = []
    top_langs: dict[str, int] = {}

    try:
        for repo in user.get_repos(type="owner", sort="updated"):
            if repo.fork:
                continue
            # Use repo.language (free, already fetched) instead of get_languages() (extra API call)
            lang = repo.language
            lang_list = [lang] if lang else []
            if lang:
                top_langs[lang] = top_langs.get(lang, 0) + repo.stargazers_count + 1

            repos.append(
                GitHubRepo(
                    name=repo.name,
                    description=repo.description,
                    url=repo.html_url,
                    stars=repo.stargazers_count,
                    languages=lang_list,
                    topics=list(repo.get_topics()),
                )
            )
            if len(repos) >= 30:
                break
    except GithubException:
        pass

    sorted_langs = sorted(top_langs, key=lambda k: top_langs[k], reverse=True)

    profile_readme: Optional[str] = None
    try:
        readme_repo = g.get_repo(f"{username}/{username}")
        readme = readme_repo.get_readme()
        profile_readme = base64.b64decode(readme.content).decode("utf-8")[:3000]
    except GithubException:
        pass

    return GitHubProfile(
        username=username,
        bio=user.bio,
        profile_readme=profile_readme,
        repos=repos,
        pinned_repos=[],
        top_languages=sorted_langs[:10],
        fetched_at=datetime.now(timezone.utc),
    )

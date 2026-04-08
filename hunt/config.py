"""Load/save config.yaml + env vars."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from .models import AppConfig

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
BATCHES_DIR = DATA_DIR / "batches"
STORY_BANK_PATH = DATA_DIR / "story_bank.json"
HISTORY_PATH = DATA_DIR / "scan-history.tsv"
PIPELINE_PATH = DATA_DIR / "pipeline.md"


def load_config() -> AppConfig:
    data: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = yaml.safe_load(f) or {}

    # Env overrides
    if os.getenv("GITHUB_TOKEN"):
        data["github_token"] = os.getenv("GITHUB_TOKEN")
    if os.getenv("GITHUB_USERNAME"):
        data["github_username"] = os.getenv("GITHUB_USERNAME")
    if os.getenv("LINKEDIN_USERNAME"):
        data["linkedin_username"] = os.getenv("LINKEDIN_USERNAME")

    try:
        return AppConfig(**data)
    except ValidationError as e:
        raise RuntimeError(f"Invalid config: {e}") from e


def save_config(cfg: AppConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Don't persist secrets to YAML
    data = cfg.model_dump(exclude={"github_token"})
    # Drop empty lists/Nones to keep config.yaml clean
    data = {k: v for k, v in data.items() if v not in (None, [], "")}
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def get_anthropic_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to your .env file or environment."
        )
    return key


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)

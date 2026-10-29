"""Loads config.json and environment secrets."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
ASSETS_DIR = ROOT / "assets"


@dataclass
class Config:
    raw: dict
    anthropic_api_key: str | None = None
    claude_code_oauth_token: str | None = None
    ig_access_token: str | None = None
    ig_user_id: str | None = None
    github_token: str | None = None
    github_repository: str | None = None
    media_branch: str = "media"
    extra: dict = field(default_factory=dict)

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)

    @property
    def color(self):
        return self.raw["colors"]


def load_config(path: Path = CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    # Allow env overrides for the most commonly tweaked values.
    if os.getenv("CLAUDE_MODEL"):
        raw["claude_model"] = os.environ["CLAUDE_MODEL"]
    if os.getenv("GRAPH_API_VERSION"):
        raw["graph_api_version"] = os.environ["GRAPH_API_VERSION"]
    return Config(
        raw=raw,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        claude_code_oauth_token=os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or None,
        ig_access_token=os.getenv("IG_ACCESS_TOKEN"),
        ig_user_id=os.getenv("IG_USER_ID") or None,
        github_token=os.getenv("GITHUB_TOKEN"),
        github_repository=os.getenv("GITHUB_REPOSITORY"),
        media_branch=os.getenv("MEDIA_BRANCH", "media"),
    )

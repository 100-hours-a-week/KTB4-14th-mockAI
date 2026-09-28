from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class Settings:
    api_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env_file: Path = ENV_FILE) -> "Settings":
        # Exported values take precedence over .env; no cwd dependency.
        values = {**dotenv_values(env_file), **os.environ}
        token = values.get("AUDIGO_API_TOKEN")
        if token and len(token) < 32:
            raise RuntimeError("AUDIGO_API_TOKEN must be at least 32 characters")
        return cls(api_token=token)

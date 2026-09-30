from functools import lru_cache
from typing import Any, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WORDLE_",
        extra="ignore",
    )

    base_url: str = Field(
        default="https://wordle.votee.dev:8000",
        description="Base URL of the Wordle API.",
    )
    timeout: float = Field(default=10.0, gt=0, description="HTTP timeout in seconds.")
    default_size: int = Field(default=5, ge=1, description="Default word size.")
    max_attempts: int = Field(default=6, ge=1, description="Maximum solver attempts.")
    verify_ssl: bool = Field(default=True, description="Verify TLS certificates.")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_settings(**overrides: Any) -> Settings:
    return Settings(**{k: v for k, v in overrides.items() if v is not None})

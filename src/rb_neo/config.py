"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Connection and data-source settings.

    Values are read from environment variables (prefix-free) or a local ``.env``.
    """

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "rbneopass"
    rb_words_dir: str = "words"

    # Optional LLM layer (Phase 4). Unset key -> deterministic offline fallback.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def get_settings() -> Settings:
    """Return a freshly loaded :class:`Settings` instance."""
    return Settings()

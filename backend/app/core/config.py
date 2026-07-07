"""Platform settings.

Phase 0 keeps configuration minimal and env/file based. Hot-path risk
limits intentionally live in risk/limits.py (owned by the risk gateway),
not here -- changing them must go through that module's audit path.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ETB_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Path("data")
    journal_dir: Path = Path("data/journal")
    bar_db_path: Path = Path("data/market_data.db")

    # Empty string = adapter disabled (journal-only / SQLite-only mode).
    redpanda_brokers: str = ""  # e.g. "localhost:9092"
    questdb_ilp_host: str = ""  # e.g. "localhost"; ILP port 9009

    # Slow-path LLM analyst -- vendor-agnostic, chosen by config (see
    # slowpath/providers.py). Default "stub" = no external model required.
    # provider: stub | anthropic | openai | groq | together | openrouter |
    #           gemini | ollama | lmstudio | deepseek | mistral | xai |
    #           openai_compatible (then set llm_base_url).
    llm_provider: str = "stub"
    llm_model: str = ""       # blank = provider's default model
    llm_api_key: str = ""     # paid endpoints; local servers ignore it
    llm_base_url: str = ""    # required for openai_compatible / custom
    llm_timeout_s: float = 30.0

    # The classic (Part-1) frontend, if you run it alongside this app. The
    # dashboard sidebar links to it (and deep-links its broker page).
    legacy_ui_url: str = "http://localhost:3000"
    legacy_ui_brokers_path: str = "/brokers"


def get_settings() -> Settings:
    return Settings()


def llm_config():
    """Build an LLMConfig from settings/env. Switching models is a config
    change (ETB_LLM_PROVIDER / ETB_LLM_MODEL / ETB_LLM_API_KEY / ...), never
    a code change."""
    from app.slowpath.providers import LLMConfig

    s = get_settings()
    return LLMConfig(
        provider=s.llm_provider,
        model=s.llm_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        timeout_s=s.llm_timeout_s,
    )

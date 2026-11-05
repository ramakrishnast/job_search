"""Application settings, loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repo root is two levels above `app/`
REPO_ROOT = Path(__file__).resolve().parents[2]

# (parse model, default model) per provider. Parsing runs on every new job → cheapest model.
PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "openai": ("gpt-5-mini", "gpt-5"),
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5"),
    "auto": ("gpt-5-mini", "gpt-5"),
}


def _has(v: str | None) -> bool:
    return bool(v and v.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="production", description="production | development | test")
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    # Storage — everything personal lives under DATA_DIR (gitignored).
    data_dir: Path = REPO_ROOT / "data"
    database_url: str | None = None

    # Config files
    companies_file: Path = REPO_ROOT / "config" / "companies.yaml"
    companies_local_file: Path = REPO_ROOT / "config" / "companies.local.yaml"
    filters_file: Path = REPO_ROOT / "config" / "filters.yaml"
    profile_file: Path = REPO_ROOT / "config" / "profile.yaml"
    profile_example_file: Path = REPO_ROOT / "config" / "profile.example.yaml"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    # LLM provider: "auto" picks OpenAI if OPENAI_API_KEY is set, else Anthropic if ANTHROPIC_API_KEY is set.
    llm_provider: Literal["auto", "openai", "anthropic"] = "auto"
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # optional: Azure OpenAI / compatible gateway
    anthropic_api_key: str | None = None
    # Models — leave empty for provider defaults (see PROVIDER_DEFAULTS).
    llm_model_parse: str | None = None
    llm_model_default: str | None = None
    llm_model_interview: str | None = None  # defaults to llm_model_default
    # Optional price override ($ per 1M tokens) for the cost guard when using a model not in the price table.
    llm_price_input_per_mtok: float | None = None
    llm_price_output_per_mtok: float | None = None
    llm_validation_retries: int = 2  # extra attempts when output fails Pydantic validation
    llm_refusal_fallback: bool = True  # server-side fallback on Sonnet/Opus 5.5 (Claude API only)
    llm_structured_outputs: bool = True  # use output_config.format JSON schema when supported
    llm_daily_budget_usd: float = 5.0  # cost guard for background (non-interactive) LLM work
    llm_timeout_seconds: float = 180.0

    # Discovery
    discovery_schedule_enabled: bool = True
    discovery_hour: int = 6
    discovery_minute: int = 43
    timezone: str = "America/New_York"
    discovery_on_startup_if_stale_hours: int = 30  # 0 disables catch-up run at startup
    auto_score_new: bool = True
    max_auto_scores_per_run: int = 25
    http_user_agent: str = "JobPilot/1.0 (personal job-search tool; respects robots.txt)"
    http_min_interval_seconds: float = 1.0  # per-host politeness delay
    http_timeout_seconds: float = 30.0
    discovery_max_workers: int = 4

    # Interview / code runner
    code_run_timeout_seconds: float = 10.0
    code_run_memory_mb: int = 512

    @field_validator("data_dir", mode="after")
    @classmethod
    def _ensure_data_dir(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        (v / "uploads").mkdir(exist_ok=True)
        (v / "exports").mkdir(exist_ok=True)
        (v / "backups").mkdir(exist_ok=True)
        return v

    @model_validator(mode="after")
    def _resolve_provider(self) -> Settings:
        if self.llm_provider == "auto":
            if _has(self.openai_api_key):
                self.llm_provider = "openai"
            elif _has(self.anthropic_api_key):
                self.llm_provider = "anthropic"
        defaults = PROVIDER_DEFAULTS.get(self.llm_provider, PROVIDER_DEFAULTS["openai"])
        self.llm_model_parse = self.llm_model_parse or defaults[0]
        self.llm_model_default = self.llm_model_default or defaults[1]
        return self

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'jobpilot.db'}"

    @property
    def interview_model(self) -> str:
        return self.llm_model_interview or self.llm_model_default or ""

    @property
    def llm_api_key(self) -> str | None:
        if self.llm_provider == "openai":
            return self.openai_api_key if _has(self.openai_api_key) else None
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key if _has(self.anthropic_api_key) else None
        return None

    @property
    def llm_configured(self) -> bool:
        return self.llm_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()

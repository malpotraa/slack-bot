"""Centralized config loaded from environment / .env.

Production note: Cloud Run injects PORT (typically 8080). We honor it via the
oauth_port field's `port` alias so that the FastAPI server binds to the right
port without code changes between local and prod.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ── App ──
    app_env: str = "prod"
    app_base_url: str = ""  # set in prod to https://<service>.run.app
    log_level: str = "INFO"
    app_secret_key: str = ""
    token_encryption_key: str = ""

    # DB — local default is SQLite; prod sets DATABASE_URL to a Postgres DSN.
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'app.db'}"

    # FastAPI bind. In Cloud Run, $PORT is injected; we accept either name.
    oauth_host: str = "0.0.0.0"
    oauth_port: int = Field(default=8080, validation_alias="PORT")

    # ── Anthropic ──
    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-4-6"
    extraction_model: str = "claude-haiku-4-5-20251001"

    # ── Slack ──
    slack_signing_secret: str = ""
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""

    # ── Google ──
    google_client_id: str = ""
    google_client_secret: str = ""
    google_ads_developer_token: str = ""
    google_ads_login_customer_id: str = ""
    google_ads_api_version: str = "v24"

    # ── Wrike ──
    wrike_client_id: str = ""
    wrike_client_secret: str = ""

    # ── Phoenix ──
    phoenix_collector_endpoint: str = "https://app.phoenix.arize.com"
    phoenix_project_name: str = "slack-assistant"
    phoenix_api_key: str = ""
    # false (default): trace prompts, tool names/args and replies in full, but
    # mask the VALUES in tool responses (shape + types only). true: trace
    # everything raw, incl. user email + Anthropic SDK auto-instrumentation.
    trace_sensitive_data: bool = False

    # ── Defaults ──
    default_workday_start: str = "09:00"
    default_workday_end: str = "18:00"

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.app_base_url}/oauth/google/callback"

    @property
    def google_ads_redirect_uri(self) -> str:
        return f"{self.app_base_url}/oauth/google-ads/callback"

    @property
    def wrike_redirect_uri(self) -> str:
        return f"{self.app_base_url}/oauth/wrike/callback"

    @property
    def slack_user_redirect_uri(self) -> str:
        return f"{self.app_base_url}/oauth/slack/callback"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

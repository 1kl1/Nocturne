from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_url: str
    database_url: str
    encryption_key: str
    notion_client_id: str
    notion_client_secret: str
    notion_redirect_uri: str
    openrouter_api_key: str
    openrouter_default_model: str
    openrouter_app_name: str
    openrouter_app_url: str
    skip_external_validation: bool
    email_provider: str
    email_from: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    web_search_provider: str
    web_search_api_key: str
    scheduler_enabled: bool
    default_user_email: str

    @property
    def database_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            raw_path = self.database_url.removeprefix("sqlite:///")
            return Path(raw_path)
        if self.database_url.startswith("sqlite:////"):
            raw_path = "/" + self.database_url.removeprefix("sqlite:////")
            return Path(raw_path)
        return Path("./data/nocturne.sqlite3")

    @property
    def notion_oauth_configured(self) -> bool:
        return bool(self.notion_client_id and self.notion_client_secret and self.notion_redirect_uri)

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key)


def get_settings() -> Settings:
    return Settings(
        app_url=os.getenv("APP_URL", "http://localhost:8000").rstrip("/"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/nocturne.sqlite3"),
        encryption_key=os.getenv("NOCTURNE_ENCRYPTION_KEY", "dev-only-nocturne-encryption-key"),
        notion_client_id=os.getenv("NOTION_CLIENT_ID", ""),
        notion_client_secret=os.getenv("NOTION_CLIENT_SECRET", ""),
        notion_redirect_uri=os.getenv("NOTION_REDIRECT_URI", "http://localhost:8000/auth/notion/callback"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_default_model=os.getenv("OPENROUTER_DEFAULT_MODEL", "openai/gpt-4.1-mini"),
        openrouter_app_name=os.getenv("OPENROUTER_APP_NAME", "Nocturne"),
        openrouter_app_url=os.getenv("OPENROUTER_APP_URL", os.getenv("APP_URL", "http://localhost:8000")),
        skip_external_validation=_bool_env("NOCTURNE_SKIP_EXTERNAL_VALIDATION", False),
        email_provider=os.getenv("EMAIL_PROVIDER", "smtp").lower(),
        email_from=os.getenv("EMAIL_FROM", "nocturne@example.com"),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        web_search_provider=os.getenv("WEB_SEARCH_PROVIDER", "none").lower(),
        web_search_api_key=os.getenv("WEB_SEARCH_API_KEY", ""),
        scheduler_enabled=_bool_env("SCHEDULER_ENABLED", True),
        default_user_email=os.getenv("DEFAULT_USER_EMAIL", "owner@example.com"),
    )

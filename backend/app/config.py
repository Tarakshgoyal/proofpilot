"""Application configuration (env-driven)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


class Settings:
    app_mode: str = os.getenv("APP_MODE", "fixture")          # live | fixture
    paypal_env: str = os.getenv("PAYPAL_ENV", "sandbox")
    paypal_base_url: str = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    paypal_webhook_id: str = os.getenv("PAYPAL_WEBHOOK_ID", "")
    paypal_buyer_email: str = os.getenv("PAYPAL_BUYER_EMAIL", "")
    # Normalize DB URLs to the psycopg2 dialect explicitly: SQLAlchemy 2.1+
    # maps a bare postgresql:// URL to psycopg3, which we don't install.
    # Handles postgres://, postgresql://, postgresql+psycopg2:// (idempotent).
    @property
    def database_url(self) -> str:
        import os as _os

        url = _os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://proofpilot:proofpilot@localhost:5432/proofpilot",
        )
        for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg2://" + url[len(prefix):]
        return url

    @property
    def sandbox_admin_enabled(self) -> bool:
        """Sandbox-only testing tools (adjudicate, require-evidence, create dispute)
        require an explicit opt-in in addition to the sandbox environment."""
        return self.paypal_env == "sandbox" and os.getenv("SANDBOX_ADMIN", "") == "true"


settings = Settings()

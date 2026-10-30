from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ROOT / "apps/api/.env"), extra="ignore")

    app_name: str = "Sahay"
    env: str = "dev"
    public_base_url: str = "http://localhost:8080"

    # Google Cloud
    gcp_project: str = ""
    vertex_location: str = "global"
    embed_location: str = "asia-south1"
    media_bucket: str = ""

    # Database: either a Cloud SQL instance (via the Python connector) or a plain DSN
    db_instance: str = ""  # project:region:instance
    db_name: str = "sahay"
    db_user: str = "sahay_app"
    db_password: str = ""
    db_owner_user: str = "postgres"
    db_owner_password: str = ""
    db_host: str = ""  # when set, connect directly (local dev) instead of via the connector
    db_port: int = 5432

    # Auth
    jwt_secret: str = "change-me"
    jwt_ttl_hours: int = 72

    # Models (one place to swap)
    model_triage: str = "gemini-3.5-flash-lite"
    model_resolver: str = "gemini-3.5-flash"
    model_composer: str = "gemini-3.5-flash"
    model_vision: str = "gemini-3.5-flash"
    model_reasoning: str = "gemini-4-argon"
    model_persona: str = "gemini-3.5-flash"
    model_voice: str = "gemini-3.5-flash-lite"  # phone turns need sub-second replies
    model_fallback: str = "gemini-3.8-flash"
    # Thinking level per stage ("" = model default). Low levels keep the live path fast.
    thinking_triage: str = "minimal"
    thinking_resolver: str = "low"
    thinking_composer: str = "minimal"
    thinking_vision: str = "low"
    thinking_reasoning: str = ""
    model_image: str = "gemini-3.1-flash-lite-image"
    model_embed: str = "gemini-embedding-001"
    embed_dims: int = 768

    # Approx prices (USD per 1M tokens) for cost tracking: "model=in/out,..."
    model_prices: str = (
        "gemini-3.5-flash-lite=0.1/0.4,gemini-3.8-flash=0.5/3,gemini-4-argon=2/10,gemini-3.5-flash=0.3/2.5"
    )
    usd_inr: float = 88.0

    # Pipeline tuning
    resolver_max_steps: int = 8
    kb_top_k: int = 5
    kb_min_score: float = 0.012  # RRF score threshold for "answerable"

    # Channels (platform-level credentials; per-tenant routing lives in DB)
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_graph_version: str = "v23.0"
    resend_api_key: str = ""
    resend_webhook_secret: str = ""
    email_from: str = ""
    inbound_email_domain: str = ""

    # Optional third parties
    firecrawl_api_key: str = ""

    # Shared secret for Cloud Scheduler → /internal/cron/*
    cron_secret: str = ""
    # Platform key for the voice pipeline when it routes calls by dialled number (per-business keys also work)
    voice_platform_key: str = ""
    # Optional Cloudflare Turnstile for the widget (free). Without it the widget uses a built-in proof-of-work check.
    turnstile_site_key: str = ""
    turnstile_secret: str = ""
    widget_pow_bits: int = 16

    # Seeding
    seed_password: str = "sahay-demo"

    @property
    def prices(self) -> dict[str, tuple[float, float]]:
        out = {}
        for item in self.model_prices.split(","):
            if "=" in item:
                name, p = item.split("=", 1)
                i, o = p.split("/")
                out[name.strip()] = (float(i), float(o))
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

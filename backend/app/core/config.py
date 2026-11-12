from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    cors_origins: str = ""  # comma separated extra origins

    # Database (Supabase Postgres connection string, or sqlite for local/tests)
    database_url: str = "sqlite:///./paypilot.db"

    # Supabase Auth (JWT verification only; service-role key is never needed by this API)
    supabase_url: str = ""
    supabase_jwt_secret: str = ""

    # PayPal (primary gateway)
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    # Razorpay (secondary gateway)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # AI
    ai_provider: str = "gemini"  # gemini | openai
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"  # gemini-2.5-flash is no longer available to new API keys
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # Number of trusted reverse proxies in front of the API (0 = none: forwarded headers are ignored). See docs/deployment.md.
    trusted_proxy_count: int = Field(0, ge=0, le=5)

    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 900

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def paypal_mode(self) -> str:
        return "sandbox" if "sandbox" in self.paypal_base_url else "live"

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def allowed_origins(self) -> list[str]:
        origins = {self.frontend_url.rstrip("/"), "http://localhost:5173", "http://127.0.0.1:5173"}
        origins.update(o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip())
        return sorted(origins)


@lru_cache
def get_settings() -> Settings:
    return Settings()

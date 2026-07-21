"""Application configuration via environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Fortuner Buying Agent"
    app_env: Literal["development", "production", "test"] = "development"
    secret_key: SecretStr = SecretStr("change-me-in-production")
    database_url: str = "sqlite:///./data/fortuner.db"
    raw_snapshot_dir: str = "./data/raw"

    # Auth (simple password gate for private tool)
    dashboard_username: str = "buyer"
    dashboard_password: SecretStr = SecretStr("fortuner")

    # Search criteria
    max_price_zar: int = 700_000
    stretch_price_zar: int = 725_000
    max_mileage_km: int = 100_000
    stretch_mileage_km: int = 110_000
    make: str = "Toyota"
    model: str = "Fortuner"
    required_drivetrain: str = "4x4"

    # Collection intervals (seconds)
    marketplace_interval_seconds: int = 3 * 60 * 60
    toyota_dealer_interval_seconds: int = 2 * 60 * 60
    independent_dealer_interval_seconds: int = 5 * 60 * 60
    detail_refresh_interval_seconds: int = 24 * 60 * 60
    market_summary_interval_seconds: int = 24 * 60 * 60
    weekly_report_interval_seconds: int = 7 * 24 * 60 * 60
    daily_digest_hour_utc: int = 6

    # Removal detection
    missed_scans_possibly_removed: int = 2
    missed_scans_removed: int = 3
    hours_until_removed: int = 24

    # Dedup thresholds
    dedup_certain: int = 90
    dedup_probable: int = 75
    dedup_possible: int = 55

    # Alerts
    deal_score_alert_threshold: int = 80
    price_reduction_alert_zar: int = 10_000
    alert_provider: Literal["log", "email", "telegram"] = "log"
    alert_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # Collector behaviour
    collector_mode: Literal["live", "fixture", "hybrid"] = "fixture"
    request_delay_seconds: float = 2.0
    request_timeout_seconds: float = 30.0
    user_agent: str = (
        "FortunerBuyingAgent/1.0 (+private research; respectful low-volume)"
    )
    enable_scheduler: bool = True

    # Scoring weights (must sum conceptually to 100 before penalties)
    score_weight_price: float = 30.0
    score_weight_spec: float = 20.0
    score_weight_mileage: float = 15.0
    score_weight_reduction: float = 15.0
    score_weight_time: float = 10.0
    score_weight_history: float = 10.0

    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()

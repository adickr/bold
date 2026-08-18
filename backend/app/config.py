"""Application configuration via environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import version_check as _version_check  # noqa: F401


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
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

    # Search / budget criteria
    # Price is soft: no hard reject. Comfort budget used for scoring/alerts only.
    max_price_zar: int = 700_000
    stretch_price_zar: int = 725_000  # legacy alias; no longer used to exclude listings
    max_mileage_km: int = 100_000
    stretch_mileage_km: int = 110_000
    make: str = "Toyota"
    model: str = "Fortuner"
    required_drivetrain: str = "4x4"
    required_fuel: str = ""  # hybrid | petrol | diesel | "" (any)
    preferred_province: str = "Western Cape"
    default_sort: str = "deal_score_desc"  # deal_score_desc | price_asc
    enforce_max_price: bool = False
    collector_max_pages: int = 15  # pages per source per collect run
    collector_results_per_page: int = 50

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
    use_playwright: bool = True
    playwright_headed: bool = False
    playwright_user_data_dir: str | None = "./data/chrome-profile-cars"
    playwright_cdp_url: str | None = None  # e.g. http://127.0.0.1:9222
    request_delay_seconds: float = 2.5
    request_timeout_seconds: float = 45.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
    enable_scheduler: bool = False

    # Scoring weights (must sum conceptually to 100 before penalties)
    score_weight_price: float = 30.0
    score_weight_spec: float = 20.0  # listing completeness; trim-neutral (no VX/GR-S boost)
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

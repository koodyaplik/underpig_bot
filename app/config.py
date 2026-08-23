from __future__ import annotations

from functools import cached_property
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_id_list(value: object) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    return [int(part.strip()) for part in str(value).split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: SecretStr = Field(
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
    )
    telegram_admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    telegram_proxy: str | None = None
    bot_default_timezone: str = "Europe/Moscow"

    voice_transcription_enabled: bool = True
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_cache_dir: str = "/models"
    max_duration: int = 900

    aeroapi_api_key: SecretStr = Field(
        validation_alias=AliasChoices("FLIGHTAWARE_AEROAPI_KEY", "AEROAPI_KEY")
    )
    aeroapi_base_url: str = Field(
        default="https://aeroapi.flightaware.com/aeroapi",
        validation_alias=AliasChoices("FLIGHTAWARE_AEROAPI_BASE_URL", "AEROAPI_BASE_URL"),
    )
    aeroapi_monthly_request_limit: int = 10_000
    aeroapi_request_reserve: int = 500
    aeroapi_hard_request_cap: int = 10_000
    aeroapi_allow_overage: bool = False
    aeroapi_billing_cycle_day: int = 1
    aeroapi_max_concurrency: int = 5
    aeroapi_extended_logging: bool = False

    database_path: str = "/data/flights.db"

    scheduler_tick_seconds: int = 30
    scheduler_batch_size: int = 20
    flight_lease_seconds: int = 120
    http_connect_timeout_seconds: float = 10.0
    http_read_timeout_seconds: float = 20.0

    max_active_subscriptions_per_user: int = 10
    max_active_tracked_flights: int = 100
    flight_commands_per_user_per_minute: int = 3
    search_cache_ttl_seconds: int = 120

    notification_time_change_threshold_minutes: int = 5
    arrival_stale_grace_hours: int = 6
    diverted_max_tracking_hours: int = 12
    max_tracking_age_hours: int = 72
    post_landing_baggage_grace_minutes: int = 30

    finished_flights_retention_days: int = 90
    raw_flight_json_retention_days: int = 14
    api_request_log_retention_days: int = 120
    notification_delivery_retention_days: int = 30
    pending_selection_ttl_minutes: int = 15

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("telegram_admin_user_ids", "telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_ids(cls, value: object) -> list[int]:
        return _parse_id_list(value)

    @field_validator("aeroapi_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_limits(self) -> Settings:
        positive_fields = {
            "aeroapi_monthly_request_limit": self.aeroapi_monthly_request_limit,
            "aeroapi_hard_request_cap": self.aeroapi_hard_request_cap,
            "aeroapi_max_concurrency": self.aeroapi_max_concurrency,
            "scheduler_tick_seconds": self.scheduler_tick_seconds,
            "scheduler_batch_size": self.scheduler_batch_size,
            "flight_lease_seconds": self.flight_lease_seconds,
            "max_duration": self.max_duration,
            "max_active_subscriptions_per_user": self.max_active_subscriptions_per_user,
            "max_active_tracked_flights": self.max_active_tracked_flights,
        }
        invalid = [name for name, value in positive_fields.items() if value <= 0]
        if invalid:
            raise ValueError(f"Values must be positive: {', '.join(invalid)}")
        if not 1 <= self.aeroapi_billing_cycle_day <= 28:
            raise ValueError("AEROAPI_BILLING_CYCLE_DAY must be between 1 and 28")
        if not 0 <= self.aeroapi_request_reserve < self.aeroapi_monthly_request_limit:
            raise ValueError("AEROAPI_REQUEST_RESERVE must be smaller than monthly limit")
        if (
            not self.aeroapi_allow_overage
            and self.aeroapi_hard_request_cap > self.aeroapi_monthly_request_limit
        ):
            raise ValueError("Hard cap cannot exceed monthly limit when overage is disabled")
        if self.flight_lease_seconds <= (
            self.http_connect_timeout_seconds + self.http_read_timeout_seconds
        ):
            raise ValueError("FLIGHT_LEASE_SECONDS must exceed the total HTTP timeout")
        try:
            ZoneInfo(self.bot_default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown BOT_DEFAULT_TIMEZONE: {self.bot_default_timezone}") from exc
        return self

    @cached_property
    def telegram_token(self) -> str:
        return self.telegram_bot_token.get_secret_value()

    @cached_property
    def aeroapi_key(self) -> str:
        return self.aeroapi_api_key.get_secret_value()

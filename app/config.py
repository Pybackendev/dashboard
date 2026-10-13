from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Наша база (PostgreSQL)
    database_url: str = "postgresql+psycopg://dashboard:dashboard@postgres:5432/dashboard"

    # Магазин OpenCart (MariaDB), только чтение
    opencart_host: str = "opencart-db"
    opencart_port: int = 3306
    opencart_user: str = "opencart"
    opencart_password: str = "opencart_pass"
    opencart_db: str = "opencart"
    opencart_prefix: str = "oc_"

    sync_interval_minutes: int = 5
    timezone: str = "Europe/Kyiv"
    currency: str = "UAH"
    excluded_statuses: list[str] = [
        "Canceled", "Canceled Reversal", "Chargeback", "Denied", "Expired",
        "Failed", "Refunded", "Reversed", "Voided",
    ]
    full_sync_interval_hours: int = 24
    github_url: str = ""
    app_version: str = "0.2.0"

    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""
    telegram_morning_report_time: str = "09:00"
    low_stock_threshold: int = 5

    demo_action_token: str = ""
    # Облачный портфолио-режим использует только Postgres, без OpenCart.
    demo_mode: bool = False
    trusted_proxy_ips: str = ""
    simulate_cooldown_seconds: int = 10
    simulate_limit_per_hour: int = 6

    # Папка с фронтендом (в Docker смонтирована в /frontend)
    frontend_dir: str = "/frontend"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url", mode="before")
    @classmethod
    def use_psycopg3(cls, value):
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return "postgresql+psycopg://" + value[len("postgres://"):]
            if value.startswith("postgresql://"):
                return "postgresql+psycopg://" + value[len("postgresql://"):]
        return value

    @property
    def telegram_chat_ids(self) -> list[int]:
        result = []
        for value in self.telegram_allowed_chat_ids.split(","):
            value = value.strip()
            if value:
                try:
                    result.append(int(value))
                except ValueError:
                    continue
        return result


settings = Settings()

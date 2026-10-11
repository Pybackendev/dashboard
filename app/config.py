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

    # Папка с фронтендом (в Docker смонтирована в /frontend)
    frontend_dir: str = "/frontend"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

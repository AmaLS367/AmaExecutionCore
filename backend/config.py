from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # General
    environment: str = "development"
    debug: bool = True
    log_level: str = "DEBUG"

    # Bybit API
    bybit_testnet: bool = True
    bybit_api_key: str = ""
    bybit_api_secret: str = ""

    # Database
    database_url: str = ""

    # Trading Engine
    trading_mode: str = "shadow"
    order_mode: str = "maker_preferred"
    use_trailing_stop: bool = False

    # Risk Management
    risk_per_trade_pct: float = 0.01
    min_rrr: float = 2.0
    max_open_positions: int = 1
    max_total_risk_exposure_pct: float = 0.03

    # Safety Guard
    max_daily_loss_pct: float = 0.03
    max_weekly_loss_pct: float = 0.05
    max_consecutive_losses: int = 3
    cooldown_hours: int = 4

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

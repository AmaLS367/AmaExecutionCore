from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # General
    environment: str = "development"
    debug: bool = True
    log_level: str = "DEBUG"

    # Bybit API — mainnet credentials
    bybit_testnet: bool = True
    bybit_api_key: str = ""
    bybit_api_secret: str = ""

    # Bybit API — testnet credentials
    bybit_testnet_api_key: str = ""
    bybit_testnet_api_secret: str = ""

    # Database
    database_url: str = ""

    @property
    def active_api_key(self) -> str:
        """Returns the API key for the currently active environment."""
        return self.bybit_testnet_api_key if self.bybit_testnet else self.bybit_api_key

    @property
    def active_api_secret(self) -> str:
        """Returns the API secret for the currently active environment."""
        return self.bybit_testnet_api_secret if self.bybit_testnet else self.bybit_api_secret

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_set(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL must be set in environment or .env file")
        return v

    @model_validator(mode="after")
    def api_keys_required_outside_shadow(self) -> "Settings":
        if self.trading_mode == "shadow":
            return self
        if self.bybit_testnet:
            if not self.bybit_testnet_api_key:
                raise ValueError(
                    "BYBIT_TESTNET_API_KEY must be set when trading_mode is not 'shadow' and bybit_testnet=True"
                )
            if not self.bybit_testnet_api_secret:
                raise ValueError(
                    "BYBIT_TESTNET_API_SECRET must be set when trading_mode is not 'shadow' and bybit_testnet=True"
                )
        else:
            if not self.bybit_api_key:
                raise ValueError(
                    "BYBIT_API_KEY must be set when trading_mode is not 'shadow' and bybit_testnet=False"
                )
            if not self.bybit_api_secret:
                raise ValueError(
                    "BYBIT_API_SECRET must be set when trading_mode is not 'shadow' and bybit_testnet=False"
                )
        return self

    # Trading Engine
    trading_mode: str = "shadow"
    order_mode: str = "maker_preferred"
    shadow_equity: float = 10_000.0
    use_trailing_stop: bool = False
    demo_close_ttl_seconds: int = 30
    demo_poll_interval_seconds: float = 1.0
    demo_testnet_symbol: str = ""
    demo_testnet_entry: float = 0.0
    demo_testnet_stop: float = 0.0
    demo_testnet_target: float = 0.0

    # Risk Management
    risk_per_trade_pct: float = 0.01
    min_rrr: float = 2.0
    max_open_positions: int = 1
    max_total_risk_exposure_pct: float = 0.03

    # Safety Guard
    max_daily_loss_pct: float = 0.03
    max_weekly_loss_pct: float = 0.05
    max_consecutive_losses: int = 3
    hard_pause_consecutive_losses: int = 5
    cooldown_hours: int = 4

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

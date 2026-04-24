from collections.abc import Iterable

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
    spot_exit_monitor_interval_seconds: float = 5.0

    # Signal loop
    signal_loop_enabled: bool = False
    signal_loop_symbols: list[str] = []
    signal_loop_strategy: str = "rsi_ema"
    signal_loop_interval: str = "15"
    signal_loop_cooldown_seconds: int = 300
    signal_loop_max_symbols_concurrent: int = 5

    # Scalping
    scalping_enabled: bool = False
    scalping_symbols: list[str] = []
    scalping_interval: str = "5"
    scalping_ws_window_size: int = 50
    scalping_cooldown_seconds: int = 120
    scalping_strategy: str = "vwap_reversion"

    # Risk Management
    risk_per_trade_pct: float = 0.01
    canary_mode: bool = False
    canary_risk_multiplier: float = 0.25
    min_rrr: float = 2.0
    max_open_positions: int = 1
    max_total_risk_exposure_pct: float = 0.03
    max_trades_per_day: int = 10

    # Safety Guard
    max_daily_loss_pct: float = 0.03
    max_weekly_loss_pct: float = 0.05
    max_consecutive_losses: int = 3
    hard_pause_consecutive_losses: int = 5
    cooldown_hours: int = 4
    market_data_max_staleness_intervals: int = 2
    market_data_staleness_grace_seconds: int = 15

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
    def database_url_must_be_set(cls, value: str) -> str:
        if not value:
            raise ValueError("DATABASE_URL must be set in environment or .env file")
        return value

    @field_validator("signal_loop_symbols", "scalping_symbols", mode="before")
    @classmethod
    def parse_symbol_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return _split_symbols(value)
        if isinstance(value, Iterable):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        raise TypeError(f"Unsupported symbol list value: {value!r}")

    @field_validator("signal_loop_strategy")
    @classmethod
    def normalize_signal_loop_strategy(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("SIGNAL_LOOP_STRATEGY must not be empty.")
        return normalized

    @field_validator("canary_risk_multiplier")
    @classmethod
    def validate_canary_risk_multiplier(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("CANARY_RISK_MULTIPLIER must be greater than zero.")
        return value

    @field_validator("market_data_max_staleness_intervals")
    @classmethod
    def validate_market_data_max_staleness_intervals(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MARKET_DATA_MAX_STALENESS_INTERVALS must be at least 1.")
        return value

    @field_validator("market_data_staleness_grace_seconds")
    @classmethod
    def validate_market_data_staleness_grace_seconds(cls, value: int) -> int:
        if value < 0:
            raise ValueError("MARKET_DATA_STALENESS_GRACE_SECONDS must be >= 0.")
        return value

    @model_validator(mode="after")
    def api_keys_required_outside_shadow(self) -> "Settings":
        if self.trading_mode == "shadow":
            return self
        if self.bybit_testnet:
            if not self.bybit_testnet_api_key:
                raise ValueError(
                    "BYBIT_TESTNET_API_KEY must be set",
                )
            if not self.bybit_testnet_api_secret:
                raise ValueError(
                    "BYBIT_TESTNET_API_SECRET must be set",
                )
        else:
            if not self.bybit_api_key:
                raise ValueError(
                    "BYBIT_API_KEY must be set",
                )
            if not self.bybit_api_secret:
                raise ValueError(
                    "BYBIT_API_SECRET must be set",
                )
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def _split_symbols(raw_value: str) -> list[str]:
    stripped = raw_value.strip()
    if not stripped:
        return []
    return [part.strip().upper() for part in stripped.split(",") if part.strip()]


settings = Settings()

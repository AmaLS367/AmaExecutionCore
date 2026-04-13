class SafetyGuardError(Exception):
    """Base exception for all safety guard errors."""


class KillSwitchActiveError(SafetyGuardError):
    """Raised when a new order is attempted while the kill switch is active."""


class CircuitBreakerTrippedError(SafetyGuardError):
    """Raised when consecutive losses exceed the configured maximum."""


class DailyLossLimitError(SafetyGuardError):
    """Raised when daily realized loss exceeds the configured percentage limit."""


class CooldownActiveError(SafetyGuardError):
    """Raised when the bot is in a post-loss cooldown period."""


class WeeklyLossLimitError(SafetyGuardError):
    """Raised when weekly realized loss exceeds the configured percentage limit."""

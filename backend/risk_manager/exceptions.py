class RiskManagerError(Exception):
    """Base exception for all risk manager errors."""


class ZeroRiskDistanceError(RiskManagerError):
    """Raised when entry price equals stop price, making position sizing impossible."""


class InvalidRiskInputError(RiskManagerError):
    """Raised when inputs to risk calculations are logically invalid (e.g. non-positive equity)."""


class BelowMinQtyError(RiskManagerError):
    """Raised when calculated quantity falls below the exchange minimum order quantity."""


class BelowMinNotionalError(RiskManagerError):
    """Raised when calculated notional value falls below the exchange minimum notional."""


class InsufficientSpotBalanceError(RiskManagerError):
    """Raised when the spot wallet lacks balance for the intended order size."""

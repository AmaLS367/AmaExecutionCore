class BybitClientError(Exception):
    """Base exception for all Bybit client errors."""


class BybitAPIError(BybitClientError):
    """Raised when Bybit API returns a non-zero retCode."""

    def __init__(self, ret_code: int, ret_msg: str) -> None:
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API error {ret_code}: {ret_msg}")


class BybitConnectionError(BybitClientError):
    """Raised on network or connection failures."""


class InvalidOrderParamsError(BybitClientError):
    """Raised when order parameters are incomplete or contradictory."""

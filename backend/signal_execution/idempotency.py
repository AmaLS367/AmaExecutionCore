from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from backend.signal_execution.schemas import ExecuteSignalRequest


@dataclass(frozen=True, slots=True)
class NormalizedExecuteSignalRequest:
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    reason: str | None
    strategy_version: str | None
    indicators_snapshot: dict[str, object] | None


def normalize_execute_signal_request(
    payload: ExecuteSignalRequest,
) -> NormalizedExecuteSignalRequest:
    return NormalizedExecuteSignalRequest(
        symbol=payload.symbol.strip().upper(),
        direction=payload.direction,
        entry=float(_normalize_decimal(payload.entry)),
        stop=float(_normalize_decimal(payload.stop)),
        target=float(_normalize_decimal(payload.target)),
        reason=_normalize_optional_text(payload.reason),
        strategy_version=_normalize_optional_text(payload.strategy_version),
        indicators_snapshot=payload.indicators_snapshot,
    )


def fingerprint_signal_request(payload: NormalizedExecuteSignalRequest) -> str:
    canonical_payload = {
        "direction": payload.direction,
        "entry": _decimal_to_string(payload.entry),
        "indicators_snapshot": payload.indicators_snapshot,
        "reason": payload.reason,
        "stop": _decimal_to_string(payload.stop),
        "strategy_version": payload.strategy_version,
        "symbol": payload.symbol,
        "target": _decimal_to_string(payload.target),
    }
    canonical_json = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_decimal(value: float) -> Decimal:
    return Decimal(str(value)).normalize()


def _decimal_to_string(value: float) -> str:
    normalized = _normalize_decimal(value)
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f").rstrip("0").rstrip(".")

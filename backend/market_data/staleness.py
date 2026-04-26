from __future__ import annotations

from datetime import UTC, datetime

from backend.market_data.contracts import MarketSnapshot
from backend.market_data.intervals import interval_to_seconds


def snapshot_age_seconds(snapshot: MarketSnapshot, *, now: datetime | None = None) -> float:
    last_candle_opened_at = snapshot.candles[-1].opened_at
    if last_candle_opened_at.tzinfo is None:
        last_candle_opened_at = last_candle_opened_at.replace(tzinfo=UTC)

    current_time = now or datetime.now(UTC)
    return (current_time - last_candle_opened_at).total_seconds()


def allowed_snapshot_staleness_seconds(
    snapshot: MarketSnapshot,
    *,
    max_staleness_intervals: int,
    grace_seconds: int,
) -> int:
    return (interval_to_seconds(snapshot.interval) * max_staleness_intervals) + grace_seconds


def is_snapshot_stale(
    snapshot: MarketSnapshot,
    *,
    max_staleness_intervals: int,
    grace_seconds: int,
    now: datetime | None = None,
) -> bool:
    return snapshot_age_seconds(snapshot, now=now) > allowed_snapshot_staleness_seconds(
        snapshot,
        max_staleness_intervals=max_staleness_intervals,
        grace_seconds=grace_seconds,
    )

from __future__ import annotations

import asyncio
import gzip
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Protocol

from backend.market_data.contracts import MarketCandle
from backend.market_data.intervals import interval_to_minutes


class SupportsKlineFetch(Protocol):
    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str,
        end: int | None = None,
    ) -> object:
        ...


@dataclass(slots=True, frozen=True)
class CandleDataset:
    symbol: str
    interval: str
    lookback_days: int
    generated_at: str
    candles: tuple[MarketCandle, ...]


def candles_for_lookback(*, interval: str, lookback_days: int) -> int:
    candles_per_day = (24 * 60) / interval_to_minutes(interval)
    return ceil(candles_per_day * lookback_days)


async def fetch_candles_with_retry(
    client: SupportsKlineFetch,
    *,
    symbol: str,
    interval: str,
    lookback_days: int,
    retries: int = 3,
    base_delay_seconds: float = 2.0,
) -> tuple[MarketCandle, ...]:
    total = candles_for_lookback(interval=interval, lookback_days=lookback_days)
    return await _fetch_candles_with_retry(
        client,
        symbol=symbol,
        interval=interval,
        total=total,
        attempt_number=1,
        attempts_remaining=retries,
        base_delay_seconds=base_delay_seconds,
    )


async def _fetch_candles_with_retry(
    client: SupportsKlineFetch,
    *,
    symbol: str,
    interval: str,
    total: int,
    attempt_number: int,
    attempts_remaining: int,
    base_delay_seconds: float,
) -> tuple[MarketCandle, ...]:
    try:
        return await asyncio.to_thread(
            fetch_candles,
            client,
            symbol=symbol,
            interval=interval,
            total=total,
        )
    except Exception:
        if attempts_remaining <= 1:
            raise
        await asyncio.sleep(base_delay_seconds * attempt_number)
        return await _fetch_candles_with_retry(
            client,
            symbol=symbol,
            interval=interval,
            total=total,
            attempt_number=attempt_number + 1,
            attempts_remaining=attempts_remaining - 1,
            base_delay_seconds=base_delay_seconds,
        )


def fetch_candles(
    client: SupportsKlineFetch,
    *,
    symbol: str,
    interval: str,
    total: int,
) -> tuple[MarketCandle, ...]:
    candles: list[MarketCandle] = []
    end_cursor: int | None = None
    while len(candles) < total:
        batch = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=min(1000, total - len(candles)),
            category="spot",
            end=end_cursor,
        )
        if not isinstance(batch, list) or not batch:
            break
        ordered_batch = sorted(batch, key=lambda candle: candle.start_time)
        candles.extend(
            MarketCandle(
                opened_at=item.start_time,
                open=item.open_price,
                high=item.high_price,
                low=item.low_price,
                close=item.close_price,
                volume=item.volume,
            )
            for item in ordered_batch
        )
        oldest_candle = ordered_batch[0]
        end_cursor = int(oldest_candle.start_time.timestamp() * 1000) - 1
        if len(batch) < min(1000, total - len(candles)):
            break
    deduped = {candle.opened_at: candle for candle in candles}
    return tuple(sorted(deduped.values(), key=lambda candle: candle.opened_at))[-total:]


def save_dataset(
    path: Path,
    *,
    symbol: str,
    interval: str,
    lookback_days: int,
    candles: tuple[MarketCandle, ...],
    generated_at: datetime | None = None,
) -> None:
    payload = {
        "symbol": symbol,
        "interval": interval,
        "lookback_days": lookback_days,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "candles": [
            {
                "opened_at": candle.opened_at.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":"))
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(raw)
        return
    path.write_text(raw, encoding="utf-8")


def load_dataset(path: Path) -> CandleDataset:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            raw_payload = handle.read()
    else:
        raw_payload = path.read_text(encoding="utf-8")

    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise TypeError(f"Dataset file must contain an object: {path}")
    raw_candles = payload.get("candles")
    if not isinstance(raw_candles, list) or not raw_candles:
        raise ValueError(f"Dataset file must contain a non-empty candles array: {path}")

    candles = tuple(
        MarketCandle(
            opened_at=datetime.fromisoformat(str(candle["opened_at"])),
            open=float(candle["open"]) if "open" in candle else float(candle["close"]),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=float(candle["close"]),
            volume=float(candle.get("volume", 0.0)),
        )
        for candle in raw_candles
        if isinstance(candle, dict)
    )
    if not candles:
        raise ValueError(f"Dataset file did not contain valid candles: {path}")

    return CandleDataset(
        symbol=str(payload["symbol"]).strip().upper(),
        interval=str(payload["interval"]).strip(),
        lookback_days=int(payload["lookback_days"]),
        generated_at=str(payload.get("generated_at", "")),
        candles=candles,
    )

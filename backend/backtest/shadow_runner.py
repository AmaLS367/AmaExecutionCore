from __future__ import annotations

from typing import Any


class ShadowRunner:
    def __init__(
        self,
        *,
        snapshot_provider: Any,
        strategy: Any,
        execution_service: Any,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._strategy = strategy
        self._execution_service = execution_service

    async def run_once(self, symbol: str) -> Any | None:
        snapshot = await self._snapshot_provider.get_snapshot(symbol)
        signal = await self._strategy.generate_signal(snapshot)
        if signal is None:
            return None
        return await self._execution_service.execute_signal(signal=signal)

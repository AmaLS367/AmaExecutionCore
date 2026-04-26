import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI

from backend.api.grid_router import router as grid_router
from backend.bybit_client.exceptions import BybitConnectionError
from backend.bybit_client.rest import BybitRESTClient
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.exchange_sync.listener import ws_listener
from backend.grid_engine.grid_advisor import GridSuggestionService
from backend.grid_engine.grid_runner import GridRunner
from backend.grid_engine.order_manager import GridOrderManager
from backend.market_data.bybit_spot import BybitSpotSnapshotProvider, SupportsBybitSpotKlines
from backend.market_data.bybit_ws_feed import BybitCandleFeed
from backend.order_executor.executor import OrderExecutor
from backend.position_manager.router import router as position_router
from backend.position_manager.service import PositionManagerService
from backend.safety_guard.router import router as safety_router
from backend.signal_execution.router import router as signal_router
from backend.signal_execution.service import ExecutionService
from backend.signal_loop.runner import SignalLoopRunner
from backend.signal_loop.ws_runner import WebSocketSignalRunner
from backend.strategy_engine.factory import build_day_trading_strategy, build_scalping_strategy
from backend.strategy_engine.service import StrategyExecutionService
from backend.task_utils import create_logged_task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _validate_runner_configuration()

    sync_engine = ExchangeSyncEngine(
        session_factory=app.state.session_factory,
        rest_client=app.state.rest_client,
    )
    app.state.exchange_sync = sync_engine
    ws_listener.start()
    sync_engine.wire(ws_listener)
    sync_engine.start_reconciliation_worker()
    spot_exit_monitor_task: asyncio.Task[None] | None = None
    if settings.trading_mode != "shadow" and hasattr(app.state.position_manager, "run_spot_exit_monitor"):
        spot_exit_monitor_task = create_logged_task(
            app.state.position_manager.run_spot_exit_monitor(
                poll_interval_seconds=settings.spot_exit_monitor_interval_seconds,
            ),
            name="spot-exit-monitor",
        )

    signal_loop_runner: SignalLoopRunner | None = None
    signal_loop_task: asyncio.Task[None] | None = None
    if settings.signal_loop_enabled and settings.signal_loop_symbols:
        strategy_service = StrategyExecutionService(
            snapshot_provider=BybitSpotSnapshotProvider(rest_client=app.state.rest_client),
            strategy=build_day_trading_strategy(
                strategy_name=settings.signal_loop_strategy,
                min_rrr=settings.min_rrr,
            ),
        )
        signal_loop_runner = SignalLoopRunner(
            strategy_service=strategy_service,
            execution_service=app.state.execution_service,
            symbols=settings.signal_loop_symbols,
            interval=settings.signal_loop_interval,
            cooldown_seconds=settings.signal_loop_cooldown_seconds,
            max_symbols_concurrent=settings.signal_loop_max_symbols_concurrent,
            session_factory=app.state.session_factory,
        )
        app.state.signal_loop_runner = signal_loop_runner
        signal_loop_task = create_logged_task(
            signal_loop_runner.run_forever(),
            name="signal-loop-runner",
        )

    scalping_runner: WebSocketSignalRunner | None = None
    scalping_task: asyncio.Task[None] | None = None
    if settings.scalping_enabled and settings.scalping_symbols:
        feed = BybitCandleFeed(
            symbols=settings.scalping_symbols,
            interval=settings.scalping_interval,
            window_size=settings.scalping_ws_window_size,
            testnet=settings.bybit_testnet,
            rest_client=app.state.rest_client,
        )
        scalping_runner = WebSocketSignalRunner(
            strategy=build_scalping_strategy(
                strategy_name=settings.scalping_strategy,
                min_rrr=settings.min_rrr,
            ),
            execution_service=app.state.execution_service,
            feed=feed,
            cooldown_seconds=settings.scalping_cooldown_seconds,
            session_factory=app.state.session_factory,
        )
        scalping_task = create_logged_task(
            scalping_runner.run_forever(),
            name="scalping-runner",
        )

    yield

    if scalping_runner is not None:
        scalping_runner.stop()
    if signal_loop_runner is not None:
        signal_loop_runner.stop()
    if hasattr(app.state.position_manager, "stop_spot_exit_monitor"):
        app.state.position_manager.stop_spot_exit_monitor()
    if scalping_task is not None:
        await asyncio.gather(scalping_task, return_exceptions=True)
    if signal_loop_task is not None:
        await asyncio.gather(signal_loop_task, return_exceptions=True)
    if spot_exit_monitor_task is not None:
        await asyncio.gather(spot_exit_monitor_task, return_exceptions=True)
    await sync_engine.stop_reconciliation_worker()
    ws_listener.stop()


class NullRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def place_order(self, **_: object) -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def cancel_order(self, **_: object) -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        raise RuntimeError("Bybit REST client is not available.")

    def get_open_orders(self, **_: object) -> list[dict[str, object]]:
        raise RuntimeError("Bybit REST client is not available.")

    def get_klines(self, **_: object) -> list[object]:
        raise RuntimeError("Bybit REST client is not available.")

    def get_ticker_price(self, symbol: str, category: str = "spot") -> float:
        del symbol, category
        raise RuntimeError("Bybit REST client is not available.")


def create_app(
    *,
    session_factory: Any = AsyncSessionLocal,
    rest_client: Any | None = None,
) -> FastAPI:
    if rest_client is None:
        try:
            rest_client = BybitRESTClient()
        except BybitConnectionError:
            rest_client = NullRestClient()

    order_executor = OrderExecutor(rest_client=rest_client)
    execution_service = ExecutionService(
        session_factory=session_factory,
        order_executor=order_executor,
    )
    position_manager = PositionManagerService(
        session_factory=session_factory,
        rest_client=rest_client,
    )
    grid_runner = GridRunner(
        session_factory=session_factory,
        order_manager=GridOrderManager(rest_client=rest_client),
        rest_client=rest_client,
    )
    grid_suggestion_service = GridSuggestionService(
        snapshot_provider=BybitSpotSnapshotProvider(rest_client=cast(SupportsBybitSpotKlines, rest_client)),
    )

    app = FastAPI(
        title="AmaExecutionCore API",
        version="0.1.0",
        description="Trading Bot execution core built on strict Risk Management rules.",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.rest_client = rest_client
    app.state.execution_service = execution_service
    app.state.position_manager = position_manager
    app.state.grid_runner = grid_runner
    app.state.grid_suggestion_service = grid_suggestion_service
    app.include_router(safety_router)
    app.include_router(signal_router)
    app.include_router(position_router)
    app.include_router(grid_router)
    return app


def _validate_runner_configuration() -> None:
    if not (settings.signal_loop_enabled and settings.scalping_enabled):
        return
    overlap = set(settings.signal_loop_symbols) & set(settings.scalping_symbols)
    if overlap:
        raise RuntimeError(f"Signal loop and scalping symbol sets overlap: {sorted(overlap)}")


app = create_app()


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Basic health check to verify the app is running and config is loaded."""
    active_key = settings.active_api_key
    obfuscated_key = f"{active_key[:4]}***" if len(active_key) > 4 else "Not Set"
    return {
        "status": "ok",
        "trading_mode": settings.trading_mode,
        "environment": settings.environment,
        "bybit_testnet": settings.bybit_testnet,
        "api_key_status": obfuscated_key,
    }

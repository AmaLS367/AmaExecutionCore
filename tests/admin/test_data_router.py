from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.admin import auth as admin_auth
from backend.config import settings
from backend.grid_engine.models import (
    GridSession,
    GridSessionStatus,
    GridSlotRecord,
)
from backend.trade_journal.models import (  # noqa: F401 — registers tables
    DailyStat,
    ExchangeSide,
    MarketType,
    SafetyState,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


@pytest.fixture(autouse=True)
def _configure() -> None:
    settings.admin_jwt_secret = "test-secret-at-least-32-characters-ok"
    settings.trading_mode = "shadow"
    settings.shadow_equity = 10_000.0


def _access_token() -> str:
    return admin_auth.create_access_token("admin")


def _make_trade(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: TradeStatus = TradeStatus.POSITION_CLOSED,
    realized_pnl: Decimal | None = Decimal("100.00"),
    symbol: str = "BTCUSDT",
) -> None:
    trade = Trade(
        id=uuid.uuid4(),
        symbol=symbol,
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.SHADOW,
        status=status,
        is_post_only=False,
        is_reduce_only=False,
        realized_pnl=realized_pnl,
        closed_at=datetime.now(UTC) if status == TradeStatus.POSITION_CLOSED else None,
    )

    async def _insert() -> None:
        async with session_factory() as session:
            session.add(trade)
            await session.commit()

    asyncio.run(_insert())


def _make_grid_session(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    symbol: str = "ETHUSDT",
    status: str = GridSessionStatus.ACTIVE.value,
) -> int:
    session_obj = GridSession(
        symbol=symbol,
        config_json={"levels": 5, "spacing_pct": 1.0},
        status=status,
    )

    async def _insert() -> int:
        async with session_factory() as session:
            session.add(session_obj)
            await session.commit()
            await session.refresh(session_obj)
            return session_obj.id

    return asyncio.run(_insert())


def _make_app(session_factory: async_sessionmaker[AsyncSession]) -> TestClient:
    from fastapi import FastAPI

    from backend.admin.data_router import make_data_router

    app = FastAPI()
    import fakeredis
    app.state.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    app.include_router(make_data_router(session_factory=session_factory))
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /admin/stats/dashboard
# ---------------------------------------------------------------------------


def test_stats_dashboard_requires_auth(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    response = client.get("/admin/stats/dashboard")
    assert response.status_code in (401, 403)


def test_stats_dashboard_returns_shadow_equity(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get("/admin/stats/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["equity"] == pytest.approx(10_000.0)
    assert data["trading_mode"] == "shadow"
    assert data["safety_guard_status"] == "OK"


# ---------------------------------------------------------------------------
# GET /admin/stats/equity-curve
# ---------------------------------------------------------------------------


def test_equity_curve_returns_points(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def _insert() -> None:
        async with sqlite_session_factory() as session:
            session.add(DailyStat(stat_date=datetime.now(UTC).date(), ending_equity=Decimal("9800.00")))
            await session.commit()

    asyncio.run(_insert())

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/stats/equity-curve?days=7",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    points = response.json()
    assert len(points) == 1
    assert points[0]["equity"] == pytest.approx(9800.0)


# ---------------------------------------------------------------------------
# GET /admin/stats/daily-pnl
# ---------------------------------------------------------------------------


def test_daily_pnl_returns_points(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def _insert() -> None:
        async with sqlite_session_factory() as session:
            session.add(DailyStat(stat_date=datetime.now(UTC).date(), net_pnl=Decimal("120.00")))
            await session.commit()

    asyncio.run(_insert())

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/stats/daily-pnl?days=7",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    points = response.json()
    assert len(points) == 1
    assert points[0]["pnl"] == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# GET /admin/trades
# ---------------------------------------------------------------------------


def test_trades_list_returns_paginated_results(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_trade(sqlite_session_factory)
    _make_trade(sqlite_session_factory)

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/trades?page=1&limit=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_trades_list_filters_by_symbol(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_trade(sqlite_session_factory, symbol="BTCUSDT")
    _make_trade(sqlite_session_factory, symbol="ETHUSDT")

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/trades?symbol=ETHUSDT",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["symbol"] == "ETHUSDT"


def test_trades_list_requires_auth(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    assert client.get("/admin/trades").status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /admin/trades/summary
# ---------------------------------------------------------------------------


def test_trades_summary_endpoint(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_trade(sqlite_session_factory, realized_pnl=Decimal("200.00"))
    _make_trade(sqlite_session_factory, realized_pnl=Decimal("-50.00"))

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/trades/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_trades"] == 2
    assert data["win_rate"] == pytest.approx(0.5)
    assert data["total_pnl"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# GET /admin/trades/open
# ---------------------------------------------------------------------------


def test_trades_open_returns_only_open_positions(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_trade(sqlite_session_factory, status=TradeStatus.POSITION_OPEN, realized_pnl=None)
    _make_trade(sqlite_session_factory, status=TradeStatus.POSITION_CLOSED, realized_pnl=Decimal("50.00"))

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/trades/open",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["status"] == "position_open"


# ---------------------------------------------------------------------------
# GET /admin/grid/sessions
# ---------------------------------------------------------------------------


def test_grid_sessions_list(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_grid_session(sqlite_session_factory, symbol="ETHUSDT")

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/grid/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    assert sessions[0]["symbol"] == "ETHUSDT"


def test_grid_sessions_requires_auth(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    assert client.get("/admin/grid/sessions").status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /admin/backtest/reports/latest
# ---------------------------------------------------------------------------


def test_backtest_report_latest_returns_empty_state_when_missing(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings.backtest_reports_dir = tmp_path.as_posix()

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/backtest/reports/latest",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "message": "No backtest report found.",
    }


def test_backtest_report_latest_returns_latest_report(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings.backtest_reports_dir = tmp_path.as_posix()
    older = tmp_path / "backtest-old.json"
    latest = tmp_path / "backtest-new.json"
    older.write_text(
        json.dumps(
            {
                "mode": "regression",
                "suite": "older",
                "generated_at": "2026-04-29T00:00:00+00:00",
                "all_passed": True,
                "results": [],
            },
        ),
        encoding="utf-8",
    )
    latest.write_text(
        json.dumps(
            {
                "strategy_name": "regime_grid_v1",
                "suite_name": "regime_grid_gate",
                "mode": "regression",
                "generated_at": "2026-04-29T00:00:01+00:00",
                "all_passed": False,
                "metadata": {"report_format_version": 2, "limitations": []},
                "scenarios": [{"name": "grid_xrpusdt_regression", "passed": False, "failure_reasons": ["x"]}],
                "results": [{"name": "grid_xrpusdt_regression", "passed": False, "failure_reasons": ["x"]}],
            },
        ),
        encoding="utf-8",
    )

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/backtest/reports/latest",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["strategy_name"] == "regime_grid_v1"
    assert data["suite_name"] == "regime_grid_gate"
    assert data["metadata"]["report_format_version"] == 2
    assert data["metadata"]["source_file"] == "backtest-new.json"


def test_backtest_report_latest_skips_malformed_newest_artifact(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings.backtest_reports_dir = tmp_path.as_posix()
    older = tmp_path / "backtest-valid.json"
    latest = tmp_path / "backtest-broken.json"
    older.write_text(
        json.dumps(
            {
                "strategy_name": "vwap_reversion",
                "suite_name": "regression",
                "mode": "regression",
                "generated_at": "2026-04-29T00:00:00+00:00",
                "all_passed": True,
                "metadata": {"report_format_version": 2, "limitations": []},
                "scenarios": [],
                "results": [],
            },
        ),
        encoding="utf-8",
    )
    latest.write_text('{"strategy_name":', encoding="utf-8")

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/backtest/reports/latest",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["strategy_name"] == "vwap_reversion"
    assert data["metadata"]["source_file"] == "backtest-valid.json"


def test_backtest_report_latest_does_not_support_path_traversal(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings.backtest_reports_dir = tmp_path.as_posix()

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/backtest/reports/latest?path=../../etc/passwd",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "message": "No backtest report found.",
    }


# ---------------------------------------------------------------------------
# GET /admin/grid/sessions by ID
# ---------------------------------------------------------------------------


def test_grid_session_detail_with_slots(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = _make_grid_session(sqlite_session_factory)

    async def _add_slot() -> None:
        async with sqlite_session_factory() as db:
            db.add(
                GridSlotRecord(
                    session_id=session_id,
                    level=0,
                    buy_price=Decimal("1000.00"),
                    sell_price=Decimal("1010.00"),
                ),
            )
            await db.commit()

    asyncio.run(_add_slot())

    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        f"/admin/grid/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == session_id
    assert len(data["slots"]) == 1
    assert data["slots"][0]["level"] == 0


def test_grid_session_detail_404(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get(
        "/admin/grid/sessions/9999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /admin/config
# ---------------------------------------------------------------------------


def test_config_endpoint_returns_settings(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.get("/admin/config", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["trading_mode"] == "shadow"
    assert "bybit_api_key" not in data
    assert "bybit_api_secret" not in data
    assert "admin_jwt_secret" not in data


# ---------------------------------------------------------------------------
# POST /admin/config/reload
# ---------------------------------------------------------------------------


def test_config_reload_returns_ok(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _make_app(sqlite_session_factory)
    token = _access_token()
    response = client.post("/admin/config/reload", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "message": "Runtime config reload is not supported. Restart the container to apply .env changes.",
    }

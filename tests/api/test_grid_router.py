from __future__ import annotations

from datetime import UTC

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.grid_engine.grid_runner import GridSessionAlreadyActiveError, GridSessionNotFoundError
from backend.main import create_app


class PassiveRestClient:
    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str = "spot",
        end: int | None = None,
    ) -> list[object]:
        from datetime import datetime, timedelta

        from backend.market_data.bybit_spot import BybitKline

        now = datetime.now(UTC)
        return [
            BybitKline(
                start_time=now - timedelta(minutes=15 * (limit - i)),
                open_price=1.0,
                high_price=1.1,
                low_price=0.9,
                close_price=1.05,
                volume=100.0,
                turnover=105.0,
            )
            for i in range(limit)
        ]


class RecordingGridRunner:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped: list[int] = []

    async def start(self, session_id: int) -> None:
        self.started.append(session_id)

    async def stop(self, session_id: int) -> None:
        self.stopped.append(session_id)


def test_grid_suggest_returns_valid_step(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())

    with TestClient(app) as client:
        response = client.post(
            "/grid/suggest",
            json={"symbol": "XRPUSDT", "capital_usdt": 20.0, "lookback_days": 30},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["step_pct"] >= 0.005
    assert payload["p_max"] > payload["p_min"]


def test_grid_create_status_and_pause_flow(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())

    with TestClient(app) as client:
        create_response = client.post(
            "/grid/create",
            json={
                "symbol": "XRPUSDT",
                "p_min": 1.80,
                "p_max": 2.20,
                "n_levels": 10,
                "capital_usdt": 20.0,
            },
        )
        assert create_response.status_code == 200
        created = create_response.json()
        session_id = created["session_id"]
        assert len(created["slots"]) == 10

        status_response = client.get(f"/grid/{session_id}/status")
        pause_response = client.post(f"/grid/{session_id}/pause")

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["completed_cycles"] == 0
    assert status_payload["status"] == "paused"
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "paused"


def test_grid_start_and_stop_call_runner(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())
    runner = RecordingGridRunner()
    app.state.grid_runner = runner

    with TestClient(app) as client:
        create_response = client.post(
            "/grid/create",
            json={
                "symbol": "XRPUSDT",
                "p_min": 1.80,
                "p_max": 2.20,
                "n_levels": 10,
                "capital_usdt": 20.0,
            },
        )
        session_id = create_response.json()["session_id"]

        start_response = client.post(f"/grid/{session_id}/start")
        stop_response = client.post(f"/grid/{session_id}/stop")

    assert start_response.status_code == 200
    assert stop_response.status_code == 200
    assert runner.started == [session_id]
    assert runner.stopped == [session_id]


class FailingGridRunner:
    async def start(self, session_id: int) -> None:
        raise GridSessionAlreadyActiveError(f"Grid session {session_id} is already active")


class MissingGridRunner:
    async def start(self, session_id: int) -> None:
        raise GridSessionNotFoundError(f"Grid session {session_id} was not found.")


def test_grid_start_already_active_returns_409(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())
    runner = FailingGridRunner()
    app.state.grid_runner = runner

    with TestClient(app) as client:
        create_response = client.post(
            "/grid/create",
            json={
                "symbol": "XRPUSDT",
                "p_min": 1.80,
                "p_max": 2.20,
                "n_levels": 10,
                "capital_usdt": 20.0,
            },
        )
        session_id = create_response.json()["session_id"]

        start_response = client.post(f"/grid/{session_id}/start")

    assert start_response.status_code == 409
    assert "already active" in start_response.json()["detail"]


def test_grid_start_missing_session_returns_404(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())
    app.state.grid_runner = MissingGridRunner()

    with TestClient(app) as client:
        start_response = client.post("/grid/999/start")

    assert start_response.status_code == 404
    assert "was not found" in start_response.json()["detail"]

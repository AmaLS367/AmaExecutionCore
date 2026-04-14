# AmaExecutionCore (Trading Bot) 📈

> A secure, auditable, and testable trading execution system for **Bybit Spot** powered by **FastAPI**.

This repository is the "Execution Core" for algorithmic trading. The main philosophy of the project is to minimize the probability of deposit destruction and strictly rely on the scaling strategy: **Shadow → Demo → Tiny Real → Controlled Scale**.

## Features
* **Risk Manager**: Strict risk boundaries (exact lot sizing using `apply_exchange_constraints`, minimum RRR validation).
* **Safety Guard**: Capital protection layers including Daily/Weekly loss limits (Circuit Breaker) and an emergency Kill Switch.
* **Trade State Machine**: Robust status tracking across signal submission, order placement, exchange sync, and close flow.
* **Idempotency**: Public request deduplication for `POST /signals/execute` via deterministic request fingerprinting, with safe replay of in-flight work.
* **Asynchronous Architecture**: Built on top of WebSocket streams, `asyncpg`, `sqlalchemy` (v2.0), and `alembic` migrations.
* **Execution API**: REST entrypoint for standardized signals at `POST /signals/execute`.
* **Runners**: Shadow runner for local one-shot pipeline validation and demo runner for opt-in Bybit testnet validation.

## Quick Start

1. Clone the repository and install dependencies (including dev tools):
   ```bash
   uv sync
   ```

2. Setup your environment variables:
   ```bash
   cp .env.example .env
   # Ensure you configure your Bybit testnet keys and PostgreSQL connection string
   ```

3. Run Database Migrations (once Alembic is initialized):
   ```bash
   uv run alembic upgrade head
   ```

4. Run the app:
   ```bash
   uv run uvicorn backend.main:app --reload
   ```

5. Execute a shadow signal:
   ```bash
   curl -X POST http://127.0.0.1:8000/signals/execute \
     -H "Content-Type: application/json" \
     -d "{\"symbol\":\"BTCUSDT\",\"direction\":\"long\",\"entry\":100,\"stop\":90,\"target\":130}"
   ```

6. Validate quality gates:
   ```bash
   uv run pytest
   uv run ruff check .
   uv run mypy backend tests
   ```

7. Optional live testnet flow:
   ```bash
   uv run pytest -m testnet
   ```
   Requires Bybit testnet credentials, PostgreSQL, `TRADING_MODE=demo`, and the `DEMO_TESTNET_*` values in `.env`.

## Documentation

You can find in-depth technical documentation in the `docs/` directory:
* [English Documentation Index](docs/en/README.md)
* [Russian Documentation Index](docs/ru/README.md)

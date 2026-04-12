# AmaExecutionCore (Trading Bot) 📈

> A secure, auditable, and testable trading execution system for **Bybit Spot** powered by **FastAPI**.

This repository is the "Execution Core" for algorithmic trading. The main philosophy of the project is to minimize the probability of deposit destruction and strictly rely on the scaling strategy: **Shadow → Demo → Tiny Real → Controlled Scale**.

## Features
* **Risk Manager**: Strict risk boundaries (exact lot sizing using `apply_exchange_constraints`, minimum RRR validation).
* **Safety Guard**: Capital protection layers including Daily/Weekly loss limits (Circuit Breaker) and an emergency Kill Switch.
* **Trade State Machine**: Robust event-driven logging for every stage of a trade (from strategy signal to PnL realization).
* **Idempotency**: Strict duplication protection via Bybit's `orderLinkId` constraints.
* **Asynchronous Architecture**: Built on top of WebSocket streams, `asyncpg`, `sqlalchemy` (v2.0), and `alembic` migrations.

## Quick Start

1. Clone the repository and install dependencies (including dev tools):
   ```bash
   pip install -e ".[dev]"
   ```

2. Setup your environment variables:
   ```bash
   cp .env.example .env
   # Ensure you configure your Bybit testnet keys and PostgreSQL connection string
   ```

3. Run Database Migrations (once Alembic is initialized):
   ```bash
   alembic upgrade head
   ```

## Documentation

You can find in-depth technical documentation in the `docs/` directory and the core planning document:
* [Project Master Plan](trading_bot_plan.md)
* [Documentation Index](docs/README.md)
* [Configuration Guide](docs/configuration.md)

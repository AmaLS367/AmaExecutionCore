# AI Agent Guidelines (AGENTS.md)

Welcome to the **AmaExecutionCore** codebase. You are an AI pair-programmer contributing to a Trading System for Bybit Spot via a FastAPI backend.

## 1. Project Philosophy & Structure
* **Goal**: Build a secure, auditable, and testable execution core. Minimizing the probability of deposit loss is prioritized over maximizing profit.
* **Location**: All python business logic lives strictly inside the `backend/` directory.
* **Tech Stack**: Python 3.10+, FastAPI, Pybit (V5), asyncpg, SQLAlchemy (2.0), Alembic, Pydantic (V2), Ruff, and Mypy.

## 2. Trading Architecture
The project strictly separates responsibilities into these pipelines:
1. `market_data` -> 2. `strategy_engine` -> 3. `risk_manager` -> 4. `safety_guard` -> 5. `order_executor` -> 6. `exchange_sync` & `trade_journal`.

* **Risk Management First**: Leverage exact boundaries. Enforce 1% trade limits and verify Minimum Risk-Reward Ratios (RRR).
* **Safety Guard**: Implements Kill Switches and Circuit Breakers (daily limit rules, max open limits).
* **No Direct Inserts**: The strategy module never places orders. It only generates abstract long/short/no-trade signals.
* **Idempotency**: All exchange orders must utilize `orderLinkId` to prevent double fills. Trade states must sync seamlessly with PostgreSQL.

## 3. Workflow Instructions for AI
1. Read the provided `backend/config.py` and `docs/configuration.md` values to understand behavior toggles.
2. Ensure strict types using `mypy`. Leave no `Any` implicitly. Format and lint strictly via `ruff`.
3. Never use generic or placeholder credentials in source code. 
4. Be precise with tool calls and edits. Do not rewrite files top-to-bottom unless implementing entirely new components. Apply edits surgically.

## 4. Git Commit Style
* **Use Conventional Commits only**: All commit messages must follow the `type(scope): subject` format whenever a scope is applicable.
* **Preferred types**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
* **Preferred scopes**: Use existing module boundaries such as `risk-manager`, `safety-guard`, `order-executor`, `exchange-sync`, `trade-journal`, `strategy-engine`, `market-data`, `backtest`, `bybit-client`, `api`.
* **Examples**:
  * `feat(safety-guard): persist kill switch and circuit breaker state`
  * `fix(order-executor): handle submit timeout as pending unknown`
  * `docs(api): document signal execution endpoint`
* **Do not use free-form commit titles** like `Update stuff`, `Add changes`, or scope-less summaries when a clear scope exists.

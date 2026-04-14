# Todo: Runtime Profiles For Multi-Strategy Operation

**Goal:** Stop manually rewriting `.env` when switching between `scalping`, `day-trading`,
and later `swing` modes, while preserving one shared source of truth for account state,
exposure, and safety controls.

---

## Motivation

One runtime config currently has to serve multiple trading styles:

- scalping
- day-trading
- swing trading

Each style wants its own recommended values for:

- symbols
- timeframe / interval
- strategy selection
- cooldowns
- risk-per-trade
- max trades per day
- daily loss limits

Editing `.env` by hand for every switch is noisy and error-prone.

---

## Target Shape

Use multiple runtime profiles instead of one mutable `.env`.

Expected deployment shape:

- one shared codebase / image
- one runtime profile for `scalping`
- one runtime profile for `day-trading`
- one runtime profile for `swing`
- profile-specific env files or Compose services

Examples:

- `scalping.env`
- `daytrading.env`
- `swing.env`

Each profile should carry its own recommended runtime values so the operator can launch
the needed mode directly without re-editing the base environment file.

---

## Shared State Requirement

These profiles are intended to run on the **same trading account**, so they must share
the same persistence and safety state.

They must share:

- the same PostgreSQL database
- the same trade journal
- the same `DailyStat`
- the same `SafetyState`
- the same open-position visibility
- the same total exposure checks
- the same kill switch / circuit breaker logic

This keeps the global limits real across all active profiles:

- `max_open_positions`
- `max_total_risk_exposure_pct`
- `max_trades_per_day`
- daily / weekly loss limits
- consecutive loss handling
- per-symbol blacklist state

Without shared state, separate profiles could each think they are within limits while the
account as a whole is already over risk.

---

## Non-Goal

This is not the idea of running three fully isolated bots with independent risk state on
the same account.

---

## Open Questions

Before enabling multi-profile live operation, define symbol ownership explicitly:

- may multiple profiles trade the same symbol at the same time?
- should profiles be restricted to disjoint symbol sets?
- do we need central symbol assignment or conflict detection at startup?

---

## Later Follow-Up

Possible later enhancement:

- a separate launcher or CLI mode that selects a profile
- optional profile defaults plus operator overrides

That is a follow-up operational UX task, not part of the core profile model itself.

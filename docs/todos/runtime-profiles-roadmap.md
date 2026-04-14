# Todo: Runtime Profiles For Multi-Strategy Operation

**Goal:** Stop manually rewriting `.env` when switching between `scalping`, `day-trading`,
and later `swing` operation modes, while keeping a **single shared source of truth**
for account state, exposure, and safety controls.

---

## Why this is needed

Right now one runtime config has to serve multiple trading styles:

- scalping
- day-trading
- swing trading

Each style wants different recommended values for:

- symbols
- timeframe / interval
- strategy selection
- cooldowns
- risk-per-trade
- max trades per day
- daily loss limits

Editing `.env` by hand every time is error-prone and operationally noisy.

---

## Preferred direction

Use **multiple runtime profiles / services** instead of one mutable `.env`.

The intended deployment shape is:

- one shared codebase / image
- multiple launch profiles or Docker Compose services
- one profile for `scalping`
- one profile for `day-trading`
- one profile for `swing` later

Each profile should provide its own recommended configuration values without requiring
manual edits before every launch.

---

## Critical constraint

These profiles are expected to run against the **same trading account**, so they must
**not** have isolated risk state.

They must share:

- the same PostgreSQL database
- the same trade journal
- the same `DailyStat`
- the same `SafetyState`
- the same open-position visibility
- the same total exposure checks
- the same kill switch / circuit breaker logic

This is required so that global limits stay real across all active profiles:

- `max_open_positions`
- `max_total_risk_exposure_pct`
- `max_trades_per_day`
- daily / weekly loss limits
- consecutive loss handling
- per-symbol blacklist state

Without shared state, separate containers could each believe they are within limits
while the account as a whole is already over risk.

---

## Non-goals

This is **not** the idea of running three fully independent bots with isolated state.

It is also **not** the idea of making the container interactive at startup with prompts
such as `use recommended config?`.

Interactive selection, if needed later, should be implemented outside the container
as a separate CLI or launcher workflow.

---

## Likely implementation shape

Operationally, this should become:

- shared application image
- multiple Compose services or profile-specific env files
- e.g. `scalping.env`, `daytrading.env`, `swing.env`
- shared DB connection
- shared execution and safety tables
- profile-specific strategy/timeframe/runtime config

The runtime must also define whether multiple profiles are allowed to trade:

- the same symbol at the same time
- different symbols only
- or a centrally assigned symbol set per profile

That policy should be explicit before enabling multi-profile live operation.

---

## Future follow-up

Possible later enhancement:

- a separate CLI mode or launcher that lets the operator choose a profile
- optional `recommended defaults` vs `custom overrides`

But this should be added **outside** container startup so deployments remain reproducible,
non-interactive, and automation-friendly.

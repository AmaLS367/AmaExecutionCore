# Understanding AmaExecutionCore: A Conceptual Guide

Welcome! Whether you are a developer looking to contribute or a trader wanting to understand how your money is managed, this guide explains the core concepts of AmaExecutionCore in plain language.

## Introduction: The Robot That Fears Losing Your Money

The central philosophy of AmaExecutionCore is **capital preservation**. Making a profit is secondary; the primary goal is to ensure you never lose your entire deposit due to a bug, a sudden market crash, or a streak of bad trades.

Think of this bot not as a gambler, but as a heavily guarded armored train. Before any trade (or "train") is allowed to leave the station and risk real money, it must pass through multiple independent checkpoints.

## 1. The Journey of a Single Trade (The Trading Pipeline)

When the system runs, a trade idea goes through a strict assembly line. Here is how it works, step-by-step (for technical details, see the [Architecture Guide](architecture.md)):

* **Market Data (The Eyes):** The bot constantly watches the market, fetching the latest prices and candle charts from the exchange (Bybit).
* **Strategy Engine (The Brain):** The brain analyzes the charts and decides if it's a good time to buy or sell. *Crucially, the Brain is never allowed to touch your money directly.* It simply generates a "Signal" (an idea).
* **Risk Manager (The Accountant):** The Signal is handed to the Accountant. The Accountant checks the math: 
  * "Are we risking exactly 1% of our total money?"
  * "Is the potential reward at least twice as big as the risk (Minimum RRR)?" 
  * If the math doesn't add up, the trade is rejected immediately.
* **Safety Guard (The Security Chief):** Next, the Security Chief checks the overall health of the system. 
  * "Have we lost too much money today?"
  * "Is the emergency Kill Switch activated?" 
  * If the system is taking too many losses, the Chief blocks the trade.
* **Order Executor (The Hands):** Only if the Accountant and the Security Chief approve does the trade reach the Hands. The Hands carefully format the order and send it to the exchange.
* **Exchange Sync & Trade Journal (The Diary):** Everything that happens—every signal, every approval, every order sent, and every result—is permanently recorded in a database (PostgreSQL). If the bot crashes, it can read the Diary to remember exactly what it was doing. (See the [Database Guide](database.md)).

## 2. Three Modes of Operation: From Sandbox to Battlefield

We never deploy untested code straight to the real market. The bot has three strict modes (configured via `TRADING_MODE` in your `.env` file, see [Configuration Guide](configuration.md)):

* **Shadow Mode (The Sandbox):** The bot runs locally on your computer. It analyzes the market, generates signals, does all the math, and records the results in the database—but it **never** sends orders to the exchange. It's perfect for testing strategies safely.
* **Demo Mode (The Proving Ground):** The bot connects to the Bybit Testnet (a fake exchange with fake money). Here, we test how the bot handles network delays, partial order fills, and real-world API quirks.
* **Real Mode (The Battlefield):** The bot connects to the real Bybit exchange with real money. This mode is only used when the system has proven itself stable in Demo Mode.

## 3. Disaster Prevention (How the Safety Guard Works)

Trading is risky, and sometimes strategies hit a losing streak. The **Safety Guard** is designed to prevent a bad day from destroying your account.

* **The Kill Switch:** An emergency button. If you see the market going crazy or suspect a bug, you can hit the Kill Switch (via an API call, see [API Guide](api.md)). The bot immediately stops opening new positions and cancels any pending orders.
* **The Circuit Breaker (Anti-Tilt):** Just like stock markets halt trading during a crash, the bot has daily and weekly loss limits (e.g., "Max 3% loss per day"). If realized losses hit this limit, the bot forcefully pauses trading for the rest of the day.
* **Cooldowns:** If the bot loses several trades in a row (e.g., 3 consecutive losses), it puts itself in a "time-out" for a few hours. This prevents the bot from "revenge trading" during bad market conditions.

## 4. Protection Against Duplication (Idempotency)

A common nightmare in algorithmic trading is a network error causing the bot to accidentally buy the same asset twice. 

AmaExecutionCore solves this using **Idempotency** (a unique ticket system). When the bot wants to buy Bitcoin, it generates a unique ID (a `orderLinkId`) and saves it in the database *before* sending the request to the exchange.

If the network drops and the bot isn't sure if the order was placed, it doesn't just guess and try again. It asks the exchange, "Did you receive the order with this exact ID?" 
* If yes, the bot syncs its state.
* If no, the bot knows it's safe to retry. 
This guarantees the bot will never accidentally double-spend your money.

---
*Ready to dive deeper? Check out the [Configuration Guide](configuration.md) to set up your environment, or the [Architecture Guide](architecture.md) for technical specifics.*
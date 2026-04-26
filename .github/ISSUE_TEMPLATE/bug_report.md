---
name: Bug Report
about: Report a bug or unexpected behavior
title: "[BUG] "
labels: bug
assignees: AmaLS367
---

## Description

A clear and concise description of the bug.

## Steps to Reproduce

1. Set `TRADING_MODE=...`
2. Send request to `POST /...`
3. Observe error

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened. Include error messages, stack traces, or unexpected output.

## Environment

| Field | Value |
|-------|-------|
| Python | 3.11.x |
| Trading Mode | `shadow` / `demo` / `live` |
| Bybit Testnet | `true` / `false` |
| OS | |
| Docker | yes / no |

## Logs

```
Paste relevant logs here (docker logs ama_bot --tail 50)
```

## Additional Context

Any other context, screenshots, or information that might help diagnose the issue.

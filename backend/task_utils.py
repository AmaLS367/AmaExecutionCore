from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger


def create_logged_task(
    coroutine: Coroutine[Any, Any, Any],
    *,
    name: str,
) -> asyncio.Task[Any]:
    task = asyncio.create_task(coroutine, name=name)
    task.add_done_callback(_log_task_failure)
    return task


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return

    exception = task.exception()
    if exception is None:
        return

    logger.opt(exception=exception).error(
        "Background task failed. task_name={}",
        task.get_name(),
    )

from __future__ import annotations

import json
from pathlib import Path

from backend.config import settings


def load_latest_backtest_report() -> dict[str, object] | None:
    reports_dir = Path(settings.backtest_reports_dir).expanduser().resolve()
    if not reports_dir.exists():
        return None
    if not reports_dir.is_dir():
        raise ValueError(f"Backtest reports path is not a directory: {reports_dir}")

    candidates = tuple(
        path
        for path in reports_dir.glob("backtest*.json")
        if path.is_file()
    )
    if not candidates:
        return None

    for candidate in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            return normalize_backtest_report_payload(payload, source_path=candidate)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def normalize_backtest_report_payload(
    payload: object,
    *,
    source_path: Path | None = None,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise TypeError("Backtest report payload must be a JSON object.")

    normalized = dict(payload)
    raw_scenarios = normalized.get("scenarios")
    if raw_scenarios is None:
        raw_scenarios = normalized.get("results", [])
        normalized["scenarios"] = raw_scenarios
    if not isinstance(raw_scenarios, list):
        raise TypeError("Backtest report scenarios must be a list.")

    suite_name = normalized.get("suite_name")
    if suite_name is None:
        suite_name = normalized.get("suite")
    normalized["suite_name"] = suite_name if isinstance(suite_name, str) else None

    strategy_name = normalized.get("strategy_name")
    if not isinstance(strategy_name, str) or not strategy_name.strip():
        normalized["strategy_name"] = _derive_strategy_name(raw_scenarios)

    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault("report_format_version", 1 if "scenarios" not in payload else 2)
    limitations = metadata.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
    metadata["limitations"] = [str(item) for item in limitations]
    if source_path is not None:
        metadata.setdefault("source_file", source_path.name)
    normalized["metadata"] = metadata
    normalized.setdefault("results", raw_scenarios)
    return normalized


def _derive_strategy_name(raw_scenarios: list[object]) -> str:
    strategy_names = {
        str(item["strategy"]).strip()
        for item in raw_scenarios
        if isinstance(item, dict)
        and isinstance(item.get("strategy"), str)
        and str(item["strategy"]).strip()
    }
    if len(strategy_names) == 1:
        return next(iter(strategy_names))
    return "mixed"

from __future__ import annotations

import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.backtest_gate import _load_raw_manifest, _resolve_suite_selection


def _manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "fixtures" / "backtest_manifest.json"


def test_suite_argument_has_highest_priority() -> None:
    raw_manifest = _load_raw_manifest(_manifest_path())

    resolved_suite, selected_names = _resolve_suite_selection(
        raw_manifest,
        suite="smoke",
        active_strategy="regime_grid_v1",
        mode="regression",
    )

    assert resolved_suite == "smoke"
    assert selected_names == {"grid_xrpusdt_smoke"}


def test_active_strategy_resolves_through_manifest_aliases() -> None:
    raw_manifest = _load_raw_manifest(_manifest_path())

    resolved_suite, selected_names = _resolve_suite_selection(
        raw_manifest,
        suite=None,
        active_strategy="regime_grid_v1",
        mode="regression",
    )

    assert resolved_suite == "regime_grid_gate"
    assert selected_names == {
        "grid_xrpusdt_regression",
        "grid_solusdt_regression",
        "grid_ethusdt_regression",
    }


def test_unknown_active_strategy_reports_available_aliases() -> None:
    raw_manifest = _load_raw_manifest(_manifest_path())

    with pytest.raises(
        ValueError,
        match=(
            r"Unknown active strategy: unknown_strategy\. "
            r"Available active strategies: regime_grid_v1, vwap_reversion_v1"
        ),
    ):
        _resolve_suite_selection(
            raw_manifest,
            suite=None,
            active_strategy="unknown_strategy",
            mode="regression",
        )


def test_default_suites_still_apply_by_mode() -> None:
    raw_manifest = _load_raw_manifest(_manifest_path())

    resolved_suite, selected_names = _resolve_suite_selection(
        raw_manifest,
        suite=None,
        active_strategy=None,
        mode="regression",
    )

    assert resolved_suite == "regression"
    assert selected_names == {
        "grid_xrpusdt_regression",
        "grid_solusdt_regression",
        "grid_ethusdt_regression",
    }


def test_legacy_fallback_returns_none_when_no_suite_source_exists() -> None:
    raw_manifest = deepcopy(_load_raw_manifest(_manifest_path()))
    raw_manifest.pop("default_suites", None)

    resolved_suite, selected_names = _resolve_suite_selection(
        raw_manifest,
        suite=None,
        active_strategy=None,
        mode="regression",
    )

    assert resolved_suite is None
    assert selected_names is None


def test_active_strategy_env_is_captured_without_backend_config_collision() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["ACTIVE_STRATEGY"] = "vwap_reversion_v1"
    code = """
from pathlib import Path
import os

from scripts.backtest_gate import _SCRIPT_ACTIVE_STRATEGY_ENV, _load_raw_manifest, _resolve_suite_selection

raw_manifest = _load_raw_manifest(Path("scripts/fixtures/backtest_manifest.json"))
resolved_suite, selected_names = _resolve_suite_selection(
    raw_manifest,
    suite=None,
    active_strategy=_SCRIPT_ACTIVE_STRATEGY_ENV,
    mode="regression",
)
assert _SCRIPT_ACTIVE_STRATEGY_ENV == "vwap_reversion_v1"
assert os.environ.get("ACTIVE_STRATEGY") is None
assert resolved_suite == "vwap_reversion_research"
assert selected_names == {
    "vwap_reversion_btcusdt_15m",
    "vwap_reversion_ethusdt_15m",
    "vwap_reversion_solusdt_15m",
    "vwap_reversion_xrpusdt_15m",
}
print("ok")
"""

    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"

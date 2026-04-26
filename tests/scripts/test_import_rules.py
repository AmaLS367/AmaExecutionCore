from __future__ import annotations

from pathlib import Path

from scripts.import_rules import analyze_backend_tree


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_import_rules_reject_strategy_engine_importing_order_executor(tmp_path: Path) -> None:
    backend_root = tmp_path / "backend"
    _write(
        backend_root / "strategy_engine" / "service.py",
        "from backend.order_executor.executor import OrderExecutor\n",
    )

    violations = analyze_backend_tree(backend_root)

    assert [violation.code for violation in violations] == ["ARCH001"]
    assert "strategy_engine" in violations[0].message
    assert "order_executor" in violations[0].message


def test_import_rules_reject_fastapi_outside_main_and_routers(tmp_path: Path) -> None:
    backend_root = tmp_path / "backend"
    _write(backend_root / "strategy_engine" / "service.py", "from fastapi import APIRouter\n")

    violations = analyze_backend_tree(backend_root)

    assert [violation.code for violation in violations] == ["ARCH003"]
    assert "FastAPI" in violations[0].message


def test_import_rules_reject_router_imports_outside_main(tmp_path: Path) -> None:
    backend_root = tmp_path / "backend"
    _write(
        backend_root / "signal_execution" / "service.py",
        "from backend.position_manager.router import router\n",
    )

    violations = analyze_backend_tree(backend_root)

    assert [violation.code for violation in violations] == ["ARCH001", "ARCH004"]
    assert any("router modules" in violation.message for violation in violations)


def test_import_rules_accept_current_backend_layout() -> None:
    violations = analyze_backend_tree(Path("backend"))

    assert violations == []

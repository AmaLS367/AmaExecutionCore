from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

RULE_CODE_LAYER_VIOLATION = "ARCH001"
RULE_CODE_INVALID_ROUTER_OWNER = "ARCH002"
RULE_CODE_FASTAPI_LEAK = "ARCH003"
RULE_CODE_ROUTER_IMPORT_LEAK = "ARCH004"

FASTAPI_ALLOWED_MODULES = {
    "backend.admin.data_router",
    "backend.admin.deps",
    "backend.admin.router",
    "backend.admin.ws_logs",
    "backend.api.grid_router",
    "backend.main",
    "backend.position_manager.router",
    "backend.safety_guard.router",
    "backend.signal_execution.router",
}
ROUTER_OWNER_MODULES = {"admin", "position_manager", "safety_guard", "signal_execution"}
SHARED_MODULES = {"config", "database", "task_utils"}
DISALLOWED_IMPORTS: dict[str, set[str]] = {
    "exchange_sync": {
        "backtest",
        "market_data",
        "position_manager",
        "signal_execution",
        "strategy_engine",
    },
    "market_data": {
        "exchange_sync",
        "order_executor",
        "position_manager",
        "risk_manager",
        "safety_guard",
        "signal_execution",
        "strategy_engine",
        "trade_journal",
    },
    "order_executor": {
        "backtest",
        "exchange_sync",
        "market_data",
        "position_manager",
        "signal_execution",
        "strategy_engine",
    },
    "position_manager": {
        "backtest",
        "bybit_client",
        "exchange_sync",
        "market_data",
        "order_executor",
        "signal_execution",
        "strategy_engine",
    },
    "risk_manager": {
        "bybit_client",
        "exchange_sync",
        "market_data",
        "order_executor",
        "position_manager",
        "safety_guard",
        "signal_execution",
        "strategy_engine",
        "trade_journal",
    },
    "safety_guard": {
        "bybit_client",
        "exchange_sync",
        "market_data",
        "order_executor",
        "position_manager",
        "signal_execution",
        "strategy_engine",
    },
    "signal_execution": {
        "backtest",
        "bybit_client",
        "exchange_sync",
        "market_data",
        "position_manager",
        "strategy_engine",
    },
    "signal_loop": {"backtest", "exchange_sync", "order_executor", "position_manager"},
    "strategy_engine": {
        "bybit_client",
        "exchange_sync",
        "order_executor",
        "position_manager",
        "risk_manager",
        "safety_guard",
        "signal_execution",
        "trade_journal",
    },
    "trade_journal": {
        "backtest",
        "bybit_client",
        "exchange_sync",
        "market_data",
        "order_executor",
        "position_manager",
        "risk_manager",
        "safety_guard",
        "signal_execution",
        "strategy_engine",
    },
}


@dataclass(slots=True, frozen=True)
class Violation:
    code: str
    file_path: Path
    line: int
    message: str


def analyze_backend_tree(backend_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for file_path in sorted(backend_root.rglob("*.py")):
        if "__pycache__" in file_path.parts:
            continue
        violations.extend(_analyze_python_file(file_path, backend_root))
    return sorted(
        violations,
        key=lambda violation: (violation.file_path.as_posix(), violation.line, violation.code),
    )


def _analyze_python_file(file_path: Path, backend_root: Path) -> list[Violation]:
    source = file_path.read_text(encoding="utf-8")
    module_name = _module_name_for_file(file_path, backend_root)
    module_group = _module_group(module_name)
    tree = ast.parse(source, filename=str(file_path))

    violations: list[Violation] = []
    if file_path.name == "router.py" and module_group not in ROUTER_OWNER_MODULES:
        violations.append(
            Violation(
                code=RULE_CODE_INVALID_ROUTER_OWNER,
                file_path=file_path,
                line=1,
                message=f"Router modules must live only under {sorted(ROUTER_OWNER_MODULES)}.",
            ),
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for imported_module in _iter_imported_modules(node):
            imported_group = _normalize_import_group(imported_module)
            if imported_group is None:
                continue
            if (
                module_group in DISALLOWED_IMPORTS
                and imported_group in DISALLOWED_IMPORTS[module_group]
                and imported_group != module_group
            ):
                violations.append(
                    Violation(
                        code=RULE_CODE_LAYER_VIOLATION,
                        file_path=file_path,
                        line=node.lineno,
                        message=f"{module_group} must not import {imported_group}.",
                    ),
                )
            if imported_group == "fastapi" and module_name not in FASTAPI_ALLOWED_MODULES:
                violations.append(
                    Violation(
                        code=RULE_CODE_FASTAPI_LEAK,
                        file_path=file_path,
                        line=node.lineno,
                        message=(
                            "FastAPI imports are allowed only in backend.main and dedicated "
                            "router modules."
                        ),
                    ),
                )
            if imported_module.endswith(".router") and module_name != "backend.main":
                violations.append(
                    Violation(
                        code=RULE_CODE_ROUTER_IMPORT_LEAK,
                        file_path=file_path,
                        line=node.lineno,
                        message="Only backend.main may import router modules directly.",
                    ),
                )
    return violations


def _iter_imported_modules(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if node.level != 0 or node.module is None:
        return ()
    if any(alias.name == "*" for alias in node.names):
        return (node.module,)
    if node.module.startswith("backend."):
        return tuple(f"{node.module}.{alias.name}" for alias in node.names)
    return (node.module,)


def _module_name_for_file(file_path: Path, backend_root: Path) -> str:
    relative_path = file_path.relative_to(backend_root)
    parts = list(relative_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("backend", *parts))


def _module_group(module_name: str) -> str:
    parts = module_name.split(".")
    if len(parts) == 1 and parts[0] == "backend":
        return "backend"
    if len(parts) < 2:
        message = f"Unexpected backend module name: {module_name}"
        raise ValueError(message)
    return parts[1]


def _normalize_import_group(imported_module: str) -> str | None:
    if imported_module.startswith("backend."):
        parts = imported_module.split(".")
        if len(parts) < 2:
            return None
        group = parts[1]
        if group in SHARED_MODULES:
            return None
        return group
    if imported_module == "fastapi" or imported_module.startswith("fastapi."):
        return "fastapi"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate clean-architecture import boundaries.")
    parser.add_argument("--backend-root", type=Path, default=Path("backend"))
    args = parser.parse_args()

    violations = analyze_backend_tree(args.backend_root)
    if not violations:
        print(f"Import rules passed for {args.backend_root}.")
        return

    for violation in violations:
        relative_path = violation.file_path.as_posix()
        print(f"{relative_path}:{violation.line}: {violation.code} {violation.message}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()

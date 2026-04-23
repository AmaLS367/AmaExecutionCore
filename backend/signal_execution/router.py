from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request

from backend.order_executor.executor import OrderAlreadySubmittedError
from backend.risk_manager.exceptions import RiskManagerError
from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_execution.schemas import ExecuteSignalRequest, ExecuteSignalResponse
from backend.signal_execution.service import ExecutionService

router = APIRouter(prefix="/signals", tags=["signals"])


def get_execution_service(request: Request) -> ExecutionService:
    return cast("ExecutionService", request.app.state.execution_service)


@router.post("/execute", response_model=ExecuteSignalResponse)
async def execute_signal(request: Request, payload: ExecuteSignalRequest) -> ExecuteSignalResponse:
    service = get_execution_service(request)
    try:
        result = await service.execute_signal(signal=payload)
    except OrderAlreadySubmittedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SafetyGuardError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except RiskManagerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ExecuteSignalResponse(
        signal_id=result.signal_id,
        trade_id=result.trade_id,
        order_link_id=result.order_link_id,
        status=result.status,
        mode=result.mode,
        replayed=result.replayed,
    )

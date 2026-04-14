from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_session
from backend.safety_guard.kill_switch import kill_switch

router = APIRouter(prefix="/safety", tags=["safety"])


@router.post("/kill")
async def activate_kill_switch(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Activates the kill switch. Cancels all pending exchange orders.
    Blocks all new order submissions until POST /safety/reset clears it.
    Does NOT close open positions — manual action required.
    """
    await kill_switch.activate(session=session, rest_client=request.app.state.rest_client)
    return {"kill_switch": True, "status": "activated"}


@router.post("/reset")
async def reset_safety_state(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    state = await kill_switch.reset(session)
    return {
        "kill_switch": state.kill_switch_active,
        "pause_reason": state.pause_reason.value if state.pause_reason else None,
        "manual_reset_required": state.manual_reset_required,
    }


@router.get("/status")
async def get_safety_status(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Returns current safety guard state."""
    state = await kill_switch.status(session)
    return {
        "kill_switch": state.kill_switch_active,
        "pause_reason": state.pause_reason.value if state.pause_reason else None,
        "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
        "manual_reset_required": state.manual_reset_required,
        "trading_mode": settings.trading_mode,
    }

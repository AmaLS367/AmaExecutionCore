from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_session
from backend.safety_guard.kill_switch import kill_switch

router = APIRouter(prefix="/safety", tags=["safety"])


@router.post("/kill")
async def activate_kill_switch(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Activates the kill switch. Cancels all pending exchange orders.
    Blocks all new order submissions until app is restarted.
    Does NOT close open positions — manual action required.
    """
    await kill_switch.activate(session=session, rest_client=None)
    return {"kill_switch": True, "status": "activated"}


@router.get("/status")
async def get_safety_status() -> dict[str, Any]:
    """Returns current safety guard state."""
    return {
        "kill_switch": kill_switch.is_active(),
        "trading_mode": settings.trading_mode,
    }

from fastapi import APIRouter

from app.api.v1.endpoints.agent import router as agent_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.markets import router as markets_router

router = APIRouter()
router.include_router(health_router)
router.include_router(markets_router)
router.include_router(agent_router)

from fastapi import APIRouter, Depends

from app.api.v1.endpoints.agent import router as agent_router
from app.api.v1.endpoints.comparisons import router as comparisons_router
from app.api.v1.endpoints.crop_risk import router as crop_risk_router
from app.api.v1.endpoints.crops import router as crops_router
from app.api.v1.endpoints.farmer import router as farmer_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.logistics import router as logistics_router
from app.api.v1.endpoints.markets import router as markets_router
from app.api.v1.endpoints.prices import router as prices_router
from app.api.v1.endpoints.produce import router as produce_router
from app.api.v1.endpoints.users import router as users_router
from app.core.auth import active_identity

router = APIRouter()
router.include_router(health_router)
router.include_router(users_router)
router.include_router(farmer_router)
router.include_router(crops_router)
router.include_router(produce_router)
router.include_router(prices_router)
router.include_router(logistics_router)
router.include_router(crop_risk_router)
router.include_router(comparisons_router)
router.include_router(markets_router, dependencies=[Depends(active_identity)])
router.include_router(agent_router, dependencies=[Depends(active_identity)])

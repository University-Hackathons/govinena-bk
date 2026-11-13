from fastapi import APIRouter

from app.agents.demo import run_demo
from app.schemas.agent import AgentDemoRequest, AgentDemoResponse

router = APIRouter(tags=["agent"])


@router.post("/agent/demo", response_model=AgentDemoResponse)
async def agent_demo(request: AgentDemoRequest) -> AgentDemoResponse:
    """Run a mock reasoning graph using synthetic prices/routes. No paid API calls."""
    return await run_demo(request)

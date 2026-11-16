# Real-data workflow is deliberately separate from /agent/demo's synthetic fixtures.
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agents.demo import run_demo
from app.core.auth import UserDependency
from app.db.session import SessionDependency
from app.models.agent_run import AgentRun
from app.schemas.agent import AgentDemoRequest, AgentDemoResponse
from app.schemas.recommendations import (
    AgentConstraints,
    ApprovalRequest,
    ResumeRequest,
    RunResponse,
)
from app.services.agent_runs import AgentRuns, execute_run, owned
from app.services.agent_runs import response as run_response

router = APIRouter(tags=["agent"])


@router.post("/agent/demo", response_model=AgentDemoResponse)
async def agent_demo(request: AgentDemoRequest) -> AgentDemoResponse:
    """Run a mock reasoning graph using synthetic prices/routes. No paid API calls."""
    return await run_demo(request)


@router.post("/agent/recommendations", response_model=RunResponse, status_code=202)
def recommendation(
    data: AgentConstraints,
    user: UserDependency,
    session: SessionDependency,
    request: Request,
    background: BackgroundTasks,
    response: Response,
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        row = AgentRuns(session, request.app.state.settings).create(user.id, data)
        result = run_response(row)
        background.add_task(execute_run, request.app, row.id)
        return result
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503) from None


@router.get("/agent/runs", response_model=list[RunResponse])
def runs(
    user: UserDependency,
    session: SessionDependency,
    response: Response,
    page: int = Query(default=1, ge=1, le=1000000),
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        rows = session.scalars(
            select(AgentRun)
            .where(AgentRun.farmer_id == user.id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .offset((page - 1) * 20)
            .limit(20)
        ).all()
        return [run_response(row) for row in rows]
    except SQLAlchemyError:
        raise HTTPException(503) from None


@router.get("/agent/runs/{id}", response_model=RunResponse)
def run(id: UUID, user: UserDependency, session: SessionDependency, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return run_response(owned(session, user.id, id))
    except SQLAlchemyError:
        raise HTTPException(503) from None


@router.post("/agent/runs/{id}/resume", response_model=RunResponse, status_code=202)
def resume(
    id: UUID,
    data: ResumeRequest,
    user: UserDependency,
    session: SessionDependency,
    request: Request,
    background: BackgroundTasks,
    response: Response,
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        row = AgentRuns(session, request.app.state.settings).resume(user.id, id, data)
        result = run_response(row)
        background.add_task(execute_run, request.app, id)
        return result
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503) from None


@router.post("/agent/runs/{id}/decision", response_model=RunResponse)
def decide(
    id: UUID,
    data: ApprovalRequest,
    user: UserDependency,
    session: SessionDependency,
    request: Request,
    response: Response,
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return run_response(
            AgentRuns(session, request.app.state.settings).decide(user.id, id, data)
        )
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503) from None

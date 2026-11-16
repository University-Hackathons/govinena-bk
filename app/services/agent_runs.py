import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from langgraph.types import Command
from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.agents.checkpoint import DurableSaver
from app.agents.market_graph import build_market_graph
from app.agents.market_reasoner import MarketReasoner
from app.agents.market_tools import VerifiedMarketTools
from app.models import AgentRun, Market, ProduceBatch
from app.providers.routes import MapboxRouteProvider
from app.repositories.logistics import LogisticsRepository
from app.repositories.produce import ProduceRepository
from app.schemas.produce import utc
from app.schemas.recommendations import AgentConstraints, RunResponse
from app.services.external import get_http_client
from app.services.logistics import LogisticsService
from app.services.market_comparison import MarketComparisonService


def response(row):
    state = row.state
    return RunResponse(
        id=row.id,
        produce_batch_id=row.produce_batch_id,
        status=row.status,
        approval=row.approval,
        revision=row.revision,
        created_at=utc(row.created_at),
        updated_at=utc(row.updated_at),
        constraints=state["constraints"],
        mode=state["mode"],
        selected_tools=state.get("selected_tools", []),
        tool_outcomes=state.get("tool_outcomes", []),
        decision_summaries=state.get("decision_summaries", []),
        missing_inputs=state.get("missing_inputs", []),
        warnings=state.get("warnings", []),
        comparison=state.get("comparison"),
        recommendation=state.get("recommendation"),
        steps=state.get("steps", 0),
    )


def owned(session, farmer_id, id, lock=False):
    query = (
        select(AgentRun)
        .where(AgentRun.id == id, AgentRun.farmer_id == farmer_id)
        .execution_options(populate_existing=True)
    )
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404)
    return row


class AgentRuns:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    def create(self, farmer_id, data: AgentConstraints):
        batch = ProduceRepository(self.session).get(farmer_id, data.produce_batch_id)
        if batch is None:
            raise HTTPException(404)
        if batch.status != "available" or utc(batch.sell_by) <= datetime.now(UTC):
            raise HTTPException(409)
        now = datetime.now(UTC)
        row = AgentRun(
            farmer_id=farmer_id,
            produce_batch_id=batch.id,
            status="queued",
            approval="pending",
            revision=0,
            created_at=now,
            updated_at=now,
            state={
                "user_id": str(farmer_id),
                "produce_batch_id": str(batch.id),
                "constraints": data.model_dump(mode="json"),
                "mode": self.settings.reasoning_mode,
                "status": "queued",
                "selected_tools": [],
                "tool_outcomes": [],
                "available_observations": [],
                "transport_results": [],
                "risk_results": [],
                "comparison": None,
                "recommendation": None,
                "explanation": None,
                "decision_summaries": [],
                "missing_inputs": [],
                "warnings": [],
                "steps": 0,
                "cycle": 0,
            },
        )
        self.session.add(row)
        self.session.commit()
        return row

    def resume(self, farmer_id, id, data):
        row = owned(self.session, farmer_id, id, lock=True)
        if row.revision != data.revision:
            raise HTTPException(409)
        now = datetime.now(UTC)
        stale = row.status == "running" and row.lease_until and utc(row.lease_until) < now
        if row.status not in {"needs_input", "failed", "queued"} and not stale:
            raise HTTPException(409)
        if row.state.get("steps", 0) >= self.settings.agent_max_steps or row.revision >= 5:
            raise HTTPException(409)
        constraints = data.constraints or AgentConstraints.model_validate(row.state["constraints"])
        if constraints.produce_batch_id != row.produce_batch_id:
            raise HTTPException(422)
        row.resume_input = (
            constraints.model_dump(mode="json")
            if data.constraints or row.status in {"needs_input", "failed"}
            else None
        )
        row.status, row.execution_token, row.lease_until = "queued", None, None
        row.revision += 1
        row.updated_at = now
        self.session.commit()
        return row

    def decide(self, farmer_id, id, data):
        row = owned(self.session, farmer_id, id, lock=True)
        desired = "accepted" if data.action == "accept" else "rejected"
        if row.approval == desired:
            return row
        if row.revision != data.revision or row.status != "completed" or row.approval != "pending":
            raise HTTPException(409)
        if data.action == "accept":
            batch = self.session.get(ProduceBatch, row.produce_batch_id)
            top = row.state["comparison"]["eligible"][0]
            now = datetime.now(UTC)
            observed = datetime.fromisoformat(top["price"]["observed_at"].replace("Z", "+00:00"))
            arrival = datetime.fromisoformat(top["arrival_at"].replace("Z", "+00:00"))
            market = self.session.get(Market, top["market_id"])
            if market is None or not market.active or not batch.crop.active:
                raise HTTPException(409)
            facts = row.state.get("batch_facts", {})
            if (
                batch.status != "available"
                or utc(batch.sell_by) <= now
                or arrival <= now
                or (now - observed).total_seconds() > self.settings.price_stale_after_hours * 3600
            ):
                raise HTTPException(409)
            if facts != batch_facts(batch):
                raise HTTPException(409)
        row.state = {
            **row.state,
            "decision_summaries": row.state.get("decision_summaries", [])
            + [f"Farmer {desired} the recommendation; no transaction performed."],
        }
        row.approval, row.updated_at = desired, datetime.now(UTC)
        row.revision += 1
        self.session.commit()
        return row


def batch_facts(batch):
    return {
        "crop_id": batch.crop_id,
        "quantity_kg": str(batch.quantity_kg),
        "quality": batch.quality,
        "harvested_at": utc(batch.harvested_at).isoformat(),
        "sell_by": utc(batch.sell_by).isoformat(),
        "updated_at": utc(batch.updated_at).isoformat(),
    }


async def execute_run(application, run_id):
    factory, settings = application.state.session_factory, application.state.settings
    token, now = uuid4(), datetime.now(UTC)
    try:
        with factory() as session:
            changed = session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    or_(
                        AgentRun.status == "queued",
                        (AgentRun.status == "running") & (AgentRun.lease_until < now),
                    ),
                )
                .values(
                    status="running", execution_token=token, lease_until=now + timedelta(minutes=10)
                )
            ).rowcount
            session.commit()
            if not changed:
                return
            row = session.get(AgentRun, run_id)
            initial, supplied, farmer_id = row.state, row.resume_input, row.farmer_id
        saver = DurableSaver(factory, run_id, token)
        async for client in get_http_client():
            with factory() as session:
                provider = None
                if settings.map_provider == "mapbox" and settings.mapbox_access_token:
                    provider = MapboxRouteProvider(
                        client,
                        settings.mapbox_access_token.get_secret_value(),
                        settings.routing_allow_mapbox_cache,
                    )
                logistics = LogisticsService(
                    LogisticsRepository(session),
                    provider,
                    application.state.route_cache,
                    settings.route_cache_ttl_seconds,
                )
                comparison = MarketComparisonService(
                    session, logistics, settings.price_stale_after_hours
                )
                tools = VerifiedMarketTools(session, farmer_id, comparison)
                run_settings = settings.model_copy(
                    update={"reasoning_mode": initial.get("mode", "mock")}
                )
                graph = build_market_graph(tools, MarketReasoner(run_settings), settings, saver)
                config = {
                    "configurable": {"thread_id": str(run_id)},
                    "recursion_limit": settings.agent_max_steps + 5,
                }
                async with asyncio.timeout(settings.agent_timeout_seconds):
                    checkpoint = await graph.aget_state(config)
                    argument = initial if not checkpoint.values else None
                    if supplied and checkpoint.values:
                        if any(task.interrupts for task in checkpoint.tasks):
                            argument = Command(resume=supplied)
                        else:
                            await graph.aupdate_state(
                                config,
                                {
                                    "constraints": supplied,
                                    "status": "running",
                                    "comparison": None,
                                    "recommendation": None,
                                    "missing_inputs": [],
                                },
                                as_node="request_missing_input",
                            )
                    result = await graph.ainvoke(argument, config)
        with factory() as session:
            row = session.get(AgentRun, run_id)
            if row.execution_token == token:
                row.state = {
                    key: value for key, value in result.items() if not key.startswith("__")
                }
                row.status = result.get("status", "failed")
                row.resume_input, row.lease_until = None, None
                row.updated_at = datetime.now(UTC)
                session.commit()
    except Exception:
        # No provider exceptions, prompts, API keys or hidden model content enter the view.
        try:
            with factory() as session:
                row = session.get(AgentRun, run_id)
                if row and row.execution_token == token:
                    row.status, row.lease_until = "failed", None
                    row.state = {
                        **row.state,
                        "warnings": row.state.get("warnings", [])
                        + [
                            "Execution stopped safely. Resume to retry from the durable checkpoint."
                        ],
                    }
                    row.updated_at = datetime.now(UTC)
                    session.commit()
        except SQLAlchemyError:
            pass  # Durable queued/running lease can be recovered when storage returns.

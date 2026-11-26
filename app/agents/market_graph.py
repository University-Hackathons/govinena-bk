import asyncio
from datetime import UTC, datetime

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.market_reasoner import ORDER, PHRASES, GroundedExplanation, ToolPlan
from app.agents.market_state import MarketAgentState
from app.agents.market_tools import InputNeeded
from app.models import FarmerProfile
from app.schemas.logistics import LogisticsResponse
from app.schemas.produce import utc
from app.schemas.recommendations import AgentConstraints, ToolTrace


def build_market_graph(tools, reasoner, settings, checkpointer):
    async def perceive(state):
        data = AgentConstraints.model_validate(state["constraints"])
        batch = tools.batch(data)
        if batch.status != "available" or utc(batch.sell_by) <= datetime.now(UTC):
            return {
                "status": "failed",
                "warnings": state["warnings"]
                + ["Only available batches before their hard deadline can be recommended."],
            }
        from app.services.agent_runs import batch_facts

        return {"status": "running", "missing_inputs": [], "batch_facts": batch_facts(batch)}

    async def completeness(state):
        data = AgentConstraints.model_validate(state["constraints"])
        profile = tools.session.get(FarmerProfile, tools.farmer_id)
        missing = []
        if profile is None:
            missing.append("Complete your farmer profile before requesting a recommendation.")
        elif data.transport.vehicle_capacity_kg is None and profile.vehicle_capacity_kg is None:
            if not data.market_overrides or any(
                not row.transport or row.transport.vehicle_capacity_kg is None
                for row in data.market_overrides
            ):
                missing.append("Enter a positive vehicle capacity for the candidate markets.")
        return {"missing_inputs": missing, "status": "needs_input" if missing else "running"}

    async def bounded_reasoning(method, validator, fallback):
        for _ in range(settings.agent_max_attempts):
            try:
                return validator.model_validate(await asyncio.wait_for(method(), 12)), False
            except Exception:
                pass  # Provider diagnostics/raw generations must not enter persisted state.
        return fallback, True

    async def plan(state):
        proposed, fallback = await bounded_reasoning(
            lambda: reasoner.plan(
                {
                    "has_overrides": bool(state["constraints"].get("market_overrides")),
                    "explicit_no_loss": state["constraints"].get("assume_no_loss", False),
                }
            ),
            ToolPlan,
            ToolPlan(tools=ORDER),
        )
        # Dependencies/order are fixed and arbitrary tool names/arguments never execute.
        requested = set(proposed.tools) | {"market_context", "market_comparison"}
        if "crop_risk" in requested:
            requested.add("transport_estimates")
        selected = [tool for tool in ORDER if tool in requested]
        return {
            "selected_tools": selected,
            "tool_index": 0,
            "transport_results": [],
            "risk_results": [],
            "available_observations": [],
            "comparison": None,
            "warnings": state["warnings"]
            + (
                [("LLM planning unavailable or invalid; deterministic verified-tool plan used.")]
                if fallback
                else []
            ),
        }

    async def execute(state):
        data = AgentConstraints.model_validate(state["constraints"])
        traces = list(state["tool_outcomes"])
        observations = state.get("available_observations", [])
        transport = state.get("transport_results", [])
        risk = state.get("risk_results", [])
        comparison = state.get("comparison")
        index = state.get("tool_index", 0)
        # One read-only tool per graph step makes outcomes durably observable.
        for name in state["selected_tools"][index : index + 1]:
            if name not in ORDER:
                return {
                    "status": "failed",
                    "warnings": state["warnings"] + ["Unsupported tool selection blocked."],
                }
            for attempt in range(1, settings.agent_max_attempts + 1):
                try:
                    if name == "market_context":
                        ids = await tools.market_context(data)
                        summary = f"Validated {len(ids)} candidate markets and owned batch context."
                    elif name == "price_observations":
                        result = await tools.price_observations(data)
                        observations = [row.model_dump(mode="json") for row in result.items]
                        summary = (
                            "Loaded recorded crop/grade/type observations with source timestamps."
                        )
                    elif name == "transport_estimates":
                        result = await tools.transport_estimates(data)
                        transport = [row.model_dump(mode="json") for row in result]
                        summary = (
                            "Evaluated capacity, route availability and explicit transport charges."
                        )
                    elif name == "crop_risk":
                        result = await tools.crop_risk(
                            data, [LogisticsResponse.model_validate(row) for row in transport]
                        )
                        risk = [row.model_dump(mode="json") for row in result]
                        summary = (
                            "Checked storage assumptions, arrival times and hard selling deadlines."
                        )
                    else:
                        result = await tools.market_comparison(data)
                        # A provider result can appear if inputs/provider change between tools.
                        if any(
                            row.transport.route.source == "mapbox" for row in result.eligible
                        ) or any(
                            row.transport and row.transport.route.source == "mapbox"
                            for row in result.excluded
                        ):
                            raise InputNeeded(
                                "Enter manual transport inputs before retaining an agent "
                                "recommendation."
                            )
                        comparison = result.model_dump(mode="json")
                        summary = (
                            "Calculated and ranked eligible net proceeds; retained "
                            "explicit exclusions."
                        )
                    traces.append(
                        ToolTrace(
                            tool=name,
                            status="success",
                            attempt=attempt,
                            summary=summary,
                            completed_at=datetime.now(UTC),
                        ).model_dump(mode="json")
                    )
                    break
                except InputNeeded as exc:
                    traces.append(
                        ToolTrace(
                            tool=name,
                            status="needs_input",
                            attempt=attempt,
                            summary=str(exc),
                            completed_at=datetime.now(UTC),
                        ).model_dump(mode="json")
                    )
                    return {
                        "tool_outcomes": traces,
                        "status": "needs_input",
                        "missing_inputs": [str(exc)],
                    }
                except Exception:
                    tools.session.rollback()
                    traces.append(
                        ToolTrace(
                            tool=name,
                            status="failed",
                            attempt=attempt,
                            summary="Verified tool failed; no unverified result was used.",
                            completed_at=datetime.now(UTC),
                        ).model_dump(mode="json")
                    )
                    if attempt == settings.agent_max_attempts:
                        return {
                            "tool_outcomes": traces,
                            "status": "failed",
                            "warnings": state["warnings"]
                            + ["Tool retries exhausted. Resume to re-check the latest data."],
                        }
        return {
            "tool_outcomes": traces,
            "tool_index": index + 1,
            "available_observations": observations,
            "transport_results": transport,
            "risk_results": risk,
            "comparison": comparison,
        }

    async def evaluate(state):
        result = state["comparison"]
        if not result or not result["eligible"]:
            questions = sorted({row["reason"] for row in result["excluded"]}) if result else []
            return {
                "status": "needs_input",
                "missing_inputs": questions
                or [
                    (
                        "No eligible market data is available. Ask an administrator "
                        "to review observations and market setup."
                    )
                ],
            }
        return {"status": "running"}

    async def missing(state):
        supplied = interrupt({"questions": state["missing_inputs"]})
        data = AgentConstraints.model_validate(supplied)
        if str(data.produce_batch_id) != state["produce_batch_id"]:
            raise HTTPException(422)
        return {
            "constraints": data.model_dump(mode="json"),
            "comparison": None,
            "recommendation": None,
            "missing_inputs": [],
            "status": "running",
            "cycle": state["cycle"] + 1,
        }

    async def recommend(state):
        top = state["comparison"]["eligible"][0]
        narrative, fallback = await bounded_reasoning(
            lambda: reasoner.explain({"rank": "highest_net", "urgency": top["risk"]["urgency"]}),
            GroundedExplanation,
            GroundedExplanation(sentences=PHRASES),
        )
        explanation = " ".join(narrative.sentences)
        return {
            "recommendation": {
                "market_id": top["market_id"],
                "market_name": top["market_name"],
                "expected_net_proceeds_lkr": top["expected_net_proceeds_lkr"],
                "explanation": explanation,
            },
            "explanation": explanation,
            "warnings": state["warnings"]
            + (
                ["LLM explanation unavailable; grounded deterministic wording used."]
                if fallback
                else []
            ),
        }

    async def persist(state):
        return {"status": "failed" if state["status"] == "failed" else "completed"}

    stages = {
        "perceive_context": perceive,
        "check_completeness": completeness,
        "plan_tools": plan,
        "execute_tools": execute,
        "evaluate_outputs": evaluate,
        "request_missing_input": missing,
        "generate_recommendation": recommend,
        "persist_result": persist,
    }
    graph = StateGraph(MarketAgentState)
    for name, action in stages.items():

        def wrapped(action=action, name=name):
            async def node(state):
                steps = min(settings.agent_max_steps, state.get("steps", 0) + 1)
                if steps >= settings.agent_max_steps and name != "persist_result":
                    return {
                        "steps": steps,
                        "status": "failed",
                        "warnings": state["warnings"] + ["Maximum workflow steps reached."],
                    }
                result = await action(state)
                return {
                    **result,
                    "steps": steps,
                    "decision_summaries": state["decision_summaries"] + [name.replace("_", " ")],
                }

            return node

        graph.add_node(name, wrapped())
    graph.add_edge(START, "perceive_context")
    chain = [
        "perceive_context",
        "check_completeness",
        "plan_tools",
        "execute_tools",
        "evaluate_outputs",
        "generate_recommendation",
        "persist_result",
    ]
    for current, following in zip(chain, chain[1:], strict=False):
        graph.add_conditional_edges(
            current,
            lambda state, following=following: (
                "persist_result"
                if state["status"] == "failed"
                else "request_missing_input"
                if state["status"] == "needs_input"
                else "execute_tools"
                if following == "evaluate_outputs"
                and state.get("tool_index", 0) < len(state["selected_tools"])
                else following
            ),
        )
    graph.add_conditional_edges(
        "request_missing_input",
        lambda state: "persist_result" if state["status"] == "failed" else "perceive_context",
    )
    graph.add_edge("persist_result", END)
    return graph.compile(checkpointer=checkpointer)

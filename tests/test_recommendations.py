from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_comparisons import BATCH, HEADERS, M1, PAYLOAD, A, comparison_app

from app.models import AgentRun, FarmerProfile, MarketPriceObservation, ProduceBatch

# Reuse the real-service integration fixture; these prices are test-only observations.
assert comparison_app


def start(client, **changes):
    result = client.post("/api/v1/agent/recommendations", headers=HEADERS, json=PAYLOAD | changes)
    assert result.status_code == 202, result.text
    return result.json()["id"]


def fetch(client, id):
    result = client.get(f"/api/v1/agent/runs/{id}", headers=HEADERS)
    assert result.status_code == 200, result.text
    return result.json()


@pytest.fixture
def client(comparison_app):
    from fastapi.testclient import TestClient

    with TestClient(comparison_app) as client:
        yield client


def test_complete_workflow_tools_order_calculator_and_decision(client, comparison_app):
    id = start(client, market_ids=[M1])
    run = fetch(client, id)
    assert run["status"] == "completed", run
    assert [row["tool"] for row in run["tool_outcomes"]] == [
        "market_context",
        "price_observations",
        "transport_estimates",
        "crop_risk",
        "market_comparison",
    ]
    assert all(row["status"] == "success" for row in run["tool_outcomes"])
    assert run["recommendation"]["market_id"] == run["comparison"]["eligible"][0]["market_id"]
    assert (
        run["recommendation"]["expected_net_proceeds_lkr"]
        == run["comparison"]["eligible"][0]["expected_net_proceeds_lkr"]
    )
    with comparison_app.state.session_factory() as session:
        saved = session.get(AgentRun, UUID(id))
        assert saved.checkpoint and "chain_of_thought" not in str(saved.state)
    accepted = client.post(
        f"/api/v1/agent/runs/{id}/decision",
        headers=HEADERS,
        json={"revision": run["revision"], "action": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["approval"] == "accepted"
    assert fetch(client, id)["approval"] == "accepted"
    assert (
        client.post(
            f"/api/v1/agent/runs/{id}/decision",
            headers=HEADERS,
            json={"revision": run["revision"], "action": "reject"},
        ).status_code
        == 409
    )


def test_missing_capacity_durable_interrupt_and_resume(client, comparison_app):
    with comparison_app.state.session_factory() as session:
        session.get(FarmerProfile, A).vehicle_capacity_kg = None
        session.commit()
    id = start(client, market_ids=[M1])
    paused = fetch(client, id)
    assert paused["status"] == "needs_input", paused
    assert "capacity" in paused["missing_inputs"][0]
    corrected = PAYLOAD | {
        "market_ids": [M1],
        "transport": PAYLOAD["transport"] | {"vehicle_capacity_kg": "1000"},
    }
    resumed = client.post(
        f"/api/v1/agent/runs/{id}/resume",
        headers=HEADERS,
        json={"revision": paused["revision"], "constraints": corrected},
    )
    assert resumed.status_code == 202, resumed.text
    assert fetch(client, id)["status"] == "completed", fetch(client, id)
    assert (
        client.post(
            f"/api/v1/agent/runs/{id}/resume",
            headers=HEADERS,
            json={"revision": paused["revision"]},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("kind", ["missing", "stale"])
def test_missing_or_stale_prices_request_input(client, comparison_app, kind):
    with comparison_app.state.session_factory() as session:
        row = session.scalar(
            select(MarketPriceObservation).where(MarketPriceObservation.market_id == M1)
        )
        if kind == "missing":
            row.price_per_kg = None
        else:
            row.observed_at = datetime.now(UTC) - timedelta(days=10)
        session.commit()
    id = start(client, market_ids=[M1])
    run = fetch(client, id)
    assert run["status"] == "needs_input", run
    assert run["recommendation"] is None and run["missing_inputs"]
    assert run["comparison"]["excluded"][0]["code"] == f"price_{kind}"


def test_rejection_persistence_and_ownership(client):
    id = start(client, market_ids=[M1])
    run = fetch(client, id)
    result = client.post(
        f"/api/v1/agent/runs/{id}/decision",
        headers=HEADERS,
        json={"revision": run["revision"], "action": "reject"},
    )
    assert result.status_code == 200 and fetch(client, id)["approval"] == "rejected"
    assert client.get(f"/api/v1/agent/runs/{id}").status_code == 401
    assert (
        client.get(f"/api/v1/agent/runs/{id}", headers={"Authorization": "Bearer b"}).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/agent/runs/{id}/decision",
            headers={"Authorization": "Bearer b"},
            json={"revision": 0, "action": "accept"},
        ).status_code
        == 404
    )
    assert client.post("/api/v1/agent/recommendations", json=PAYLOAD).status_code == 401
    assert (
        client.post(
            "/api/v1/agent/recommendations", headers={"Authorization": "Bearer b"}, json=PAYLOAD
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/agent/runs/{uuid4()}", headers=HEADERS).status_code == 404


def test_llm_unavailable_bounded_fallback(client, comparison_app, monkeypatch):
    from app.agents.market_reasoner import MarketReasoner

    counts = {"plan": 0, "explain": 0}

    async def plan(*args):
        counts["plan"] += 1
        raise RuntimeError("private key must not be exposed")

    async def explain(*args):
        counts["explain"] += 1
        raise RuntimeError("private prompt must not be exposed")

    monkeypatch.setattr(MarketReasoner, "plan", plan)
    monkeypatch.setattr(MarketReasoner, "explain", explain)
    id = start(client, market_ids=[M1])
    run = fetch(client, id)
    assert run["status"] == "completed", run
    assert counts == {"plan": 2, "explain": 2}
    assert len(run["warnings"]) == 2 and "private" not in str(run)


def test_tool_retry_limits_and_resume_after_failure(client, comparison_app, monkeypatch):
    from app.agents.market_tools import VerifiedMarketTools

    original = VerifiedMarketTools.market_comparison
    calls = [0]

    async def fail(*args):
        calls[0] += 1
        raise RuntimeError("tool offline")

    monkeypatch.setattr(VerifiedMarketTools, "market_comparison", fail)
    id = start(client, market_ids=[M1])
    run = fetch(client, id)
    assert run["status"] == "failed", run
    assert calls[0] == 2
    assert [
        row["attempt"] for row in run["tool_outcomes"] if row["tool"] == "market_comparison"
    ] == [1, 2]
    monkeypatch.setattr(VerifiedMarketTools, "market_comparison", original)
    result = client.post(
        f"/api/v1/agent/runs/{id}/resume", headers=HEADERS, json={"revision": run["revision"]}
    )
    assert result.status_code == 202
    assert fetch(client, id)["status"] == "completed", fetch(client, id)


def test_invalid_constraints_and_step_limit(client, comparison_app):
    assert (
        client.post(
            "/api/v1/agent/recommendations", headers=HEADERS, json=PAYLOAD | {"tools": ["sell"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/agent/recommendations",
            headers=HEADERS,
            json=PAYLOAD | {"market_ids": [str(i) for i in range(6)]},
        ).status_code
        == 422
    )
    id = start(client, market_ids=[M1], assume_no_loss=False)
    with comparison_app.state.session_factory() as session:
        row = session.get(AgentRun, UUID(id))
        row.state = {**row.state, "steps": 40}
        session.commit()
    assert (
        client.post(
            f"/api/v1/agent/runs/{id}/resume", headers=HEADERS, json={"revision": 0}
        ).status_code
        == 409
    )


def test_no_duplicate_workers_and_catalog_list(client, comparison_app):
    id = start(client, market_ids=[M1])
    result = client.get("/api/v1/agent/runs", headers=HEADERS)
    assert result.status_code == 200 and result.json()[0]["id"] == id
    assert client.get("/api/v1/agent/runs?page=0", headers=HEADERS).status_code == 422
    with comparison_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_route_failure_needs_input_without_fabricated_distance(client):
    id = start(client, market_ids=[M1], transport={"route_mode": "automatic"})
    run = fetch(client, id)
    assert run["status"] == "needs_input", run
    assert run["recommendation"] is None
    assert run["comparison"]["excluded"][0]["code"] == "transport_cost_unknown"


def test_mapbox_outputs_never_enter_checkpoint(client, comparison_app, monkeypatch):
    from app.agents.market_tools import VerifiedMarketTools

    # Final comparison's retention guard is independently exercised by returning a
    # Mapbox-marked verified comparator output; no provider output reaches persisted state.
    async def comparison(self, data):
        result = await self.comparison.compare(self.farmer_id, data, persist=False)
        result.eligible[0].transport.route.source = "mapbox"
        result.eligible[0].transport.route.distance_km = "12345.678"
        return result

    monkeypatch.setattr(VerifiedMarketTools, "market_comparison", comparison)
    id = start(client, market_ids=[M1])
    run = fetch(client, id)
    assert run["status"] == "needs_input" and run["comparison"] is None
    with comparison_app.state.session_factory() as session:
        row = session.get(AgentRun, UUID(id))
        assert "12345.678" not in str(row.state)
        # Binary serialized graph values are never exposed to the API.
        assert "checkpoint" not in run


def test_bounded_step_termination_and_invalid_llm_tool(client, comparison_app, monkeypatch):
    from app.agents.market_reasoner import MarketReasoner

    async def invalid(*args):
        return {"tools": ["sell_produce"]}

    monkeypatch.setattr(MarketReasoner, "plan", invalid)
    run = fetch(client, start(client, market_ids=[M1]))
    assert run["status"] == "completed"
    assert "sell_produce" not in run["selected_tools"]
    assert any("planning" in message for message in run["warnings"])
    comparison_app.state.settings.agent_max_steps = 5
    limited = fetch(client, start(client, market_ids=[M1]))
    assert limited["status"] == "failed", limited
    assert limited["steps"] <= 5
    assert any("Maximum workflow" in text for text in limited["warnings"])


def test_approval_blocks_changed_batch_and_rejection_is_idempotent(client, comparison_app):
    id = start(client, market_ids=[M1])
    with comparison_app.state.session_factory() as session:
        session.get(ProduceBatch, BATCH).quantity_kg = 50
        session.commit()
    result = client.post(
        f"/api/v1/agent/runs/{id}/decision",
        headers=HEADERS,
        json={"revision": 0, "action": "accept"},
    )
    assert result.status_code == 409
    rejected = client.post(
        f"/api/v1/agent/runs/{id}/decision",
        headers=HEADERS,
        json={"revision": 0, "action": "reject"},
    )
    assert rejected.status_code == 200
    assert (
        client.post(
            f"/api/v1/agent/runs/{id}/decision",
            headers=HEADERS,
            json={"revision": 0, "action": "reject"},
        ).status_code
        == 200
    )


def test_provider_free_mock_and_typed_plan_selection(client, monkeypatch):
    from app.agents.market_reasoner import MarketReasoner, ToolPlan

    async def minimal(*args):
        return ToolPlan(tools=["market_comparison"])

    monkeypatch.setattr(MarketReasoner, "plan", minimal)
    run = fetch(client, start(client, market_ids=[M1]))
    assert run["status"] == "completed", run
    assert run["selected_tools"] == ["market_context", "market_comparison"]
    assert [trace["tool"] for trace in run["tool_outcomes"]] == run["selected_tools"]


def test_execution_timeout_stops_without_unverified_recommendation(
    client, comparison_app, monkeypatch
):
    import asyncio

    from app.agents.market_tools import VerifiedMarketTools

    async def slow(*args):
        await asyncio.sleep(1)

    monkeypatch.setattr(VerifiedMarketTools, "market_context", slow)
    comparison_app.state.settings.agent_timeout_seconds = 0.05
    run = fetch(client, start(client, market_ids=[M1]))
    assert run["status"] == "failed" and run["recommendation"] is None
    assert any("stopped safely" in text for text in run["warnings"])


def test_unknown_market_requests_catalog_selection(client):
    run = fetch(client, start(client, market_ids=["unknown"]))
    assert run["status"] == "needs_input"
    assert run["tool_outcomes"][0]["tool"] == "market_context"
    assert run["tool_outcomes"][0]["status"] == "needs_input"
    assert run["recommendation"] is None

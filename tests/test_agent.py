from decimal import Decimal
from uuid import UUID

import pytest

from app.agents.demo import run_demo
from app.agents.provider import MockReasoner, ProviderConfigurationError, create_reasoner
from app.core.auth import Identity, active_identity
from app.core.config import Settings
from app.schemas.agent import AgentDemoRequest, TransportEstimate
from app.schemas.market import MarketPrice
from app.services.profitability import ProfitabilityCalculator


@pytest.fixture(autouse=True)
def authenticated_demo(application):
    application.dependency_overrides[active_identity] = lambda: Identity(
        UUID(int=1), UUID(int=2), "demo@example.test"
    )


def test_markets_endpoint(client):
    response = client.get("/api/v1/markets")
    assert response.status_code == 200
    body = response.json()
    assert body["data_source"] == "synthetic"
    assert len(body["markets"]) == 2
    assert all(market["data_source"] == "synthetic" for market in body["markets"])


def test_agent_endpoint_without_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/api/v1/agent/demo", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["reasoning_mode"] == "mock"
    assert body["data_source"] == "synthetic"
    assert body["recommendation"]["market_id"] == "demo-b"
    assert Decimal(body["recommendation"]["estimated_profit"]) == Decimal("12400.00")
    assert body["monitoring_status"] == "demo_complete_no_background_monitoring"
    schema = client.get("/openapi.json").json()
    assert "/api/v1/agent/demo" in schema["paths"]
    assert "requestBody" in schema["paths"]["/api/v1/agent/demo"]["post"]


@pytest.mark.parametrize("payload", [{"quantity_kg": 0}, {"crop": "rice"}, {"mode": "openai"}])
def test_demo_validation(client, payload):
    assert client.post("/api/v1/agent/demo", json=payload).status_code == 422


async def test_graph_is_repeatable_and_has_all_stages():
    request = AgentDemoRequest()
    first = await run_demo(request)
    second = await run_demo(request)
    assert first == second
    assert [step.stage for step in first.decision_history] == [
        "PERCEIVE",
        "PLAN_DECIDE",
        "SELECT_TOOLS",
        "EXECUTE_TOOLS",
        "EVALUATE_RESULTS",
        "RECOMMEND",
        "MONITOR_REEVALUATE",
    ]
    assert len(first.tool_execution_results) == 3


async def test_bounded_monitor_re_evaluation():
    result = await run_demo(AgentDemoRequest(re_evaluate_once=True))
    assert result.re_evaluation_count == 1
    assert len(result.decision_history) == 13
    assert len(result.tool_execution_results) == 6


def test_mock_provider_needs_no_key():
    settings = Settings(_env_file=None, openai_api_key=None, reasoning_mode="mock")
    assert isinstance(create_reasoner(settings), MockReasoner)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_api_key_in_live_mode(key):
    settings = Settings(_env_file=None, openai_api_key=key, reasoning_mode="openai")
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        create_reasoner(settings)


def test_missing_model_in_live_mode():
    settings = Settings(
        _env_file=None, openai_api_key="test-only", openai_model=None, reasoning_mode="openai"
    )
    with pytest.raises(ProviderConfigurationError, match="OPENAI_MODEL"):
        create_reasoner(settings)


def test_provider_uses_environment_configuration(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    monkeypatch.setenv("GOVINENA_REASONING_MODE", "openai")
    settings = Settings(_env_file=None)
    # Constructor only; no LLM invocation or external request.
    provider = create_reasoner(settings)
    assert provider.model_name == "configured-model"
    assert "test-only" not in repr(settings)


def test_decimal_arithmetic_and_rounding():
    result = ProfitabilityCalculator().calculate(
        MarketPrice(market_id="test", name="Synthetic", price_per_kg=Decimal("0.335")),
        Decimal("3"),
        TransportEstimate(market_id="test", distance_km=Decimal("0"), cost=Decimal("0.10")),
        Decimal("0.20"),
    )
    assert result.revenue == Decimal("1.01")
    assert result.estimated_profit == Decimal("0.71")

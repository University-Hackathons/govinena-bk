"""A real LangGraph run using exclusively deterministic synthetic tools."""

from langgraph.graph import END, START, StateGraph

from app.agents.provider import MockReasoner
from app.agents.state import AgentState
from app.schemas.agent import (
    AgentDemoRequest,
    AgentDemoResponse,
    DecisionRecord,
    ToolExecutionResult,
)
from app.services.notification import NoOpNotificationService
from app.services.profitability import ProfitabilityCalculator
from app.services.recommendation import RecommendationService
from app.tools.market import (
    SyntheticMarketPriceTool,
    SyntheticRouteEstimationTool,
    SyntheticTransportCostTool,
)


def record(stage: str, summary: str) -> dict:
    return {"decision_history": [DecisionRecord(stage=stage, summary=summary)]}


def build_demo_graph():
    # Deliberately independent of provider settings: this endpoint can never make a paid call.
    reasoner = MockReasoner()
    prices_tool = SyntheticMarketPriceTool()
    route_tool = SyntheticRouteEstimationTool()
    cost_tool = SyntheticTransportCostTool()
    calculator = ProfitabilityCalculator()
    recommender = RecommendationService()
    notifications = NoOpNotificationService()

    def perceive(state: AgentState):
        return record("PERCEIVE", "Accepted validated farmer input for synthetic comparison.")

    def plan(state: AgentState):
        return record("PLAN_DECIDE", "Compare synthetic net returns with deterministic arithmetic.")

    def select_tools(state: AgentState):
        return {
            "selected_tools": reasoner.plan(),
            **record("SELECT_TOOLS", "Selected synthetic price, route, and transport tools."),
        }

    def execute_tools(state: AgentState):
        prices = prices_tool.execute()
        estimates = [
            cost_tool.execute(
                price.market_id, route_tool.execute(state["farmer_location"], price.market_id)
            )
            for price in prices
        ]
        return {
            "market_prices": prices,
            "transportation_estimates": estimates,
            "tool_execution_results": [
                ToolExecutionResult(tool=tool, summary="Used fixed synthetic fixtures only.")
                for tool in state["selected_tools"]
            ],
            **record("EXECUTE_TOOLS", "Loaded synthetic prices, distances, and trip costs."),
        }

    def evaluate(state: AgentState):
        estimates = {item.market_id: item for item in state["transportation_estimates"]}
        results = [
            calculator.calculate(
                price, state["quantity_kg"], estimates[price.market_id], state["production_cost"]
            )
            for price in state["market_prices"]
        ]
        return {
            "profitability": results,
            **record("EVALUATE_RESULTS", "Computed revenue minus transport and production costs."),
        }

    def recommend(state: AgentState):
        return {
            "recommendation": recommender.recommend(state["profitability"]),
            **record("RECOMMEND", "Ranked synthetic results; this is not real farming advice."),
        }

    def monitor(state: AgentState):
        again = state["re_evaluate_once"] and state["re_evaluation_count"] == 0
        notifications.notify(state["recommendation"])
        return {
            "needs_re_evaluation": again,
            "re_evaluation_count": state["re_evaluation_count"] + int(again),
            "monitoring_status": "demo_complete_no_background_monitoring",
            **record(
                "MONITOR_REEVALUATE",
                "Requested one synthetic re-evaluation."
                if again
                else "Demo complete. No background monitoring or notifications started.",
            ),
        }

    graph = StateGraph(AgentState)
    nodes = {
        "perceive": perceive,
        "plan_decide": plan,
        "select_tools": select_tools,
        "execute_tools": execute_tools,
        "evaluate_results": evaluate,
        "recommend": recommend,
        "monitor": monitor,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)
    stages = list(nodes)
    graph.add_edge(START, stages[0])
    for current, following in zip(stages, stages[1:], strict=False):
        graph.add_edge(current, following)
    graph.add_conditional_edges(
        "monitor",
        lambda state: "repeat" if state["needs_re_evaluation"] else "done",
        {"repeat": "plan_decide", "done": END},
    )
    return graph.compile()


async def run_demo(request: AgentDemoRequest) -> AgentDemoResponse:
    initial = {
        **request.model_dump(),
        "decision_history": [],
        "tool_execution_results": [],
        "re_evaluation_count": 0,
    }
    result = await build_demo_graph().ainvoke(initial, config={"recursion_limit": 20})
    return AgentDemoResponse.model_validate(result)

from app.schemas.agent import ProfitabilityResult, Recommendation


class RecommendationService:
    def recommend(self, results: list[ProfitabilityResult]) -> Recommendation:
        if not results:
            raise ValueError("At least one evaluated market is required")
        best = sorted(results, key=lambda item: (-item.estimated_profit, item.market_id))[0]
        return Recommendation(
            market_id=best.market_id,
            estimated_profit=best.estimated_profit,
            explanation="Highest synthetic estimated profit; ties use market ID. "
            "Includes only provided production cost and synthetic transport. "
            "Deadline, capacity, spoilage, fees, and market availability are not modeled. "
            "A negative result indicates an estimated loss, not advice to sell.",
        )

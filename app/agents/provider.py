"""Lazy provider construction: import/startup and mock runs need no paid key."""

from langchain_openai import ChatOpenAI

from app.core.config import Settings


class ProviderConfigurationError(ValueError):
    pass


class MockReasoner:
    def plan(self) -> list[str]:
        return ["market_prices", "route_estimation", "transport_cost"]


def create_reasoner(settings: Settings) -> MockReasoner | ChatOpenAI:
    if settings.reasoning_mode == "mock":
        return MockReasoner()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value().strip():
        raise ProviderConfigurationError("OPENAI_API_KEY is required for OpenAI reasoning")
    if not settings.openai_model or not settings.openai_model.strip():
        raise ProviderConfigurationError("OPENAI_MODEL is required for OpenAI reasoning")
    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout=30,
        max_retries=2,
    )

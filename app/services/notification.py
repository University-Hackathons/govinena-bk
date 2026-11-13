from typing import Protocol

from app.schemas.agent import Recommendation


class NotificationService(Protocol):
    def notify(self, recommendation: Recommendation) -> bool: ...


class NoOpNotificationService:
    def notify(self, recommendation: Recommendation) -> bool:
        """Placeholder. No email, SMS, or external side effect."""
        return False

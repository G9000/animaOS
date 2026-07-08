from __future__ import annotations

from abc import ABC, abstractmethod

from anima_server.services.ingestion.models import (
    IngestionAdapterResult,
    SourceIdentity,
)


class IngestionAdapter(ABC):
    """Adapter contract for source extraction into normalized artifacts and spans."""

    name = "adapter"

    @abstractmethod
    def extract(self, identity: SourceIdentity) -> IngestionAdapterResult:
        """Extract source artifacts and citable spans."""

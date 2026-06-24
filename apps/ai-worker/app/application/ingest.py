from __future__ import annotations

import logging

from app.api.schemas import ConsumePayload
from app.domain.ports import IIngestRepository, ISagaRepository

logger = logging.getLogger(__name__)


class IngestUseCase:
    def __init__(self, ingest_repo: IIngestRepository, saga_repo: ISagaRepository) -> None:
        self.ingest_repo = ingest_repo
        self.saga_repo = saga_repo

    async def execute(self, payload: ConsumePayload) -> None:
        """Idempotent ingest: insert the webhook payload into ingest_queue."""
        await self.ingest_repo.insert_idempotent(payload)

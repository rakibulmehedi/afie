from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.models import SagaStatus
from app.db.saga import advance_saga, create_saga, mark_ingest_processed


class SagaRepository:
    async def create(self, conn: object, ingest_id: uuid.UUID, tenant_id: uuid.UUID) -> uuid.UUID:
        return await create_saga(conn, ingest_id, tenant_id)  # type: ignore[arg-type]

    async def advance(
        self,
        conn: object,
        saga_id: uuid.UUID,
        tenant_id: uuid.UUID,
        from_status: SagaStatus,
        to_status: SagaStatus,
        version: int,
        deadline_at: datetime | None = None,
    ) -> bool:
        return await advance_saga(conn, saga_id, tenant_id, from_status, to_status, version, deadline_at)  # type: ignore[arg-type]

    async def mark_ingest_processed(
        self, conn: object, ingest_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        await mark_ingest_processed(conn, ingest_id, tenant_id)  # type: ignore[arg-type]

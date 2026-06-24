from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
import uuid

from app.domain.models import SagaStatus


@runtime_checkable
class ISagaRepository(Protocol):
    async def create(self, conn: object, ingest_id: uuid.UUID, tenant_id: uuid.UUID) -> uuid.UUID: ...
    async def advance(
        self,
        conn: object,
        saga_id: uuid.UUID,
        tenant_id: uuid.UUID,
        from_status: SagaStatus,
        to_status: SagaStatus,
        version: int,
        deadline_at: datetime | None = None,
    ) -> bool: ...
    async def mark_ingest_processed(
        self, conn: object, ingest_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None: ...


@runtime_checkable
class IIngestRepository(Protocol):
    async def insert_idempotent(self, payload: object) -> None: ...


@runtime_checkable
class ISynthesisPort(Protocol):
    async def synthesize(self, system_prompt: str, sanitized_payload: str, model: str) -> str: ...


@runtime_checkable
class IPayloadGuard(Protocol):
    def sanitize(self, source: str, raw_payload: str) -> str: ...

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import uuid


class SagaStatus(StrEnum):
    RECEIVED = "RECEIVED"
    SYNTHESIZING = "SYNTHESIZING"
    DRAFTED = "DRAFTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    DISTRIBUTING = "DISTRIBUTING"
    PARTIALLY_DISTRIBUTED = "PARTIALLY_DISTRIBUTED"
    DISTRIBUTED = "DISTRIBUTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class IngestEvent:
    tenant_id: uuid.UUID
    source: str
    idempotency_key: str
    raw_payload: str


@dataclass(frozen=True)
class Saga:
    id: uuid.UUID
    tenant_id: uuid.UUID
    ingest_id: uuid.UUID
    status: SagaStatus
    version: int
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class Draft:
    tenant_id: uuid.UUID
    saga_id: uuid.UUID
    blueprint_id: object
    llm_output: str

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ConsumePayload(BaseModel):
    tenant_id: UUID
    source: Literal["github", "telegram"]
    idempotency_key: str = Field(min_length=1, max_length=512)
    raw_payload: str  # JSON string from QStash envelope

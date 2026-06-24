from __future__ import annotations

from app.db.ingest import ingest_to_db


class IngestRepository:
    async def insert_idempotent(self, payload: object) -> None:
        await ingest_to_db(payload)  # type: ignore[arg-type]

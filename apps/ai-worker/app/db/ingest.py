import logging

from app.api.schemas import ConsumePayload

logger = logging.getLogger(__name__)


async def ingest_to_db(payload: ConsumePayload) -> None:
    """Insert a consumed message into ingest_queue, idempotently.

    Acquires its own connection from the pool so it is safe to call from a
    background task (avoids use-after-return of a request-scoped connection).

    RLS Model C: SET LOCAL must be the first statement in the transaction.
    ON CONFLICT DO NOTHING makes the insert idempotent against
    uq_ingest_idempotency (tenant_id, source, idempotency_key).

    Errors are logged but NOT re-raised — the caller has already returned
    HTTP 200 to QStash, so re-raising would trigger a false retry.
    """
    from app.infrastructure.db.session import pool  # late import avoids circular at module load

    if pool is None:
        logger.error(
            "ingest_to_db called before pool is initialised — "
            "idempotency_key=%s tenant_id=%s",
            payload.idempotency_key,
            payload.tenant_id,
        )
        return

    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SET LOCAL app.current_tenant = %s",
                    (str(payload.tenant_id),),
                )
                await conn.execute(
                    """
                    INSERT INTO ingest_queue (tenant_id, source, idempotency_key, raw_payload)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (tenant_id, source, idempotency_key) DO NOTHING
                    """,
                    (
                        str(payload.tenant_id),
                        payload.source,
                        payload.idempotency_key,
                        payload.raw_payload,
                    ),
                )
    except Exception:
        logger.exception(
            "Failed to ingest message to DB — idempotency_key=%s tenant_id=%s",
            payload.idempotency_key,
            payload.tenant_id,
        )

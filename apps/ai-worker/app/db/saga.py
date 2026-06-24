from __future__ import annotations

import logging
import uuid

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)


async def create_saga(
    conn: AsyncConnection,
    ingest_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> uuid.UUID:
    """Insert a new saga row and its initial RECEIVED event.

    The caller is responsible for managing the transaction and setting
    ``SET LOCAL app.current_tenant`` before calling this function.

    Raises on UNIQUE violation if ``ingest_id`` is already claimed — the
    caller should handle ``psycopg.errors.UniqueViolation``.
    """
    cursor = await conn.execute(
        """
        INSERT INTO feature_sagas (tenant_id, ingest_id, status, version)
        VALUES (%s, %s, 'RECEIVED', 0)
        RETURNING id
        """,
        (str(tenant_id), str(ingest_id)),
    )
    row = await cursor.fetchone()
    assert row is not None  # RETURNING id always returns a row on success
    saga_id: uuid.UUID = row[0]

    await conn.execute(
        """
        INSERT INTO feature_saga_events (saga_id, tenant_id, from_status, to_status, actor)
        VALUES (%s, %s, NULL, 'RECEIVED', 'system')
        """,
        (str(saga_id), str(tenant_id)),
    )

    return saga_id


async def advance_saga(
    conn: AsyncConnection,
    saga_id: uuid.UUID,
    tenant_id: uuid.UUID,
    from_status: str,
    to_status: str,
    version: int,
) -> bool:
    """Attempt an optimistic-lock transition on the saga FSM.

    Updates ``feature_sagas`` only when the current ``status`` and ``version``
    match ``from_status`` / ``version``.  If the UPDATE touches one row,
    records the transition event and returns ``True``.  If zero rows are
    updated (another worker already advanced the saga), returns ``False``.

    The DB trigger ``trg_saga_fsm`` raises if the transition is illegal.

    The caller manages the transaction and ``SET LOCAL`` context.
    """
    cursor = await conn.execute(
        """
        UPDATE feature_sagas
           SET status     = %s,
               version    = version + 1,
               updated_at = now()
         WHERE id         = %s
           AND tenant_id  = %s
           AND version    = %s
           AND status     = %s
        """,
        (to_status, str(saga_id), str(tenant_id), version, from_status),
    )
    if cursor.rowcount == 1:
        await conn.execute(
            """
            INSERT INTO feature_saga_events
                        (saga_id, tenant_id, from_status, to_status, actor)
            VALUES (%s, %s, %s, %s, 'system')
            """,
            (str(saga_id), str(tenant_id), from_status, to_status),
        )
        return True

    return False


async def mark_ingest_processed(
    conn: AsyncConnection,
    ingest_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    """Mark an ingest_queue row as PROCESSED.

    The caller manages the transaction and ``SET LOCAL`` context.
    """
    await conn.execute(
        """
        UPDATE ingest_queue
           SET status       = 'PROCESSED',
               processed_at = now()
         WHERE id        = %s
           AND tenant_id = %s
        """,
        (str(ingest_id), str(tenant_id)),
    )

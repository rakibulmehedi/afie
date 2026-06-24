"""
Tests for the Saga FSM database layer — app/db/saga.py.

Unit tests mock the psycopg AsyncConnection so they run without a real DB.
Integration tests require DATABASE_URL and are marked @pytest.mark.integration.

asyncio_mode = "auto" is set in pyproject.toml — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Integration-test skip marker (mirrors tests/test_consume.py)
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get("DATABASE_URL", "")
_SKIP_INTEGRATION = pytest.mark.skipif(
    not _DB_URL,
    reason="DATABASE_URL not set; skipping integration tests (run with a live DB)",
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT_ID = uuid.uuid4()
INGEST_ID = uuid.uuid4()
SAGA_ID = uuid.uuid4()


def _make_mock_conn(rowcount: int = 1, fetchone_result: tuple | None = None) -> AsyncMock:
    """Return an AsyncMock that behaves like a psycopg AsyncConnection.

    ``conn.execute(...)`` returns an AsyncMock cursor whose ``rowcount``
    attribute is set to *rowcount* and whose ``fetchone()`` coroutine returns
    *fetchone_result*.
    """
    cursor = AsyncMock()
    cursor.rowcount = rowcount
    cursor.fetchone = AsyncMock(return_value=fetchone_result)

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=cursor)
    return conn


# ===========================================================================
# UNIT TESTS
# ===========================================================================


class TestCreateSaga:
    """Unit tests for app.db.saga.create_saga."""

    async def test_create_saga_returns_uuid(self) -> None:
        """create_saga must return the UUID echoed back from RETURNING id."""
        expected_id = uuid.uuid4()
        conn = _make_mock_conn(fetchone_result=(expected_id,))

        from app.db.saga import create_saga

        result = await create_saga(conn, INGEST_ID, TENANT_ID)

        assert result == expected_id

    async def test_create_saga_executes_two_inserts(self) -> None:
        """create_saga must call conn.execute exactly twice: saga + event."""
        expected_id = uuid.uuid4()
        conn = _make_mock_conn(fetchone_result=(expected_id,))

        from app.db.saga import create_saga

        await create_saga(conn, INGEST_ID, TENANT_ID)

        assert conn.execute.await_count == 2


class TestAdvanceSaga:
    """Unit tests for app.db.saga.advance_saga."""

    async def test_advance_saga_returns_true_on_rowcount_1(self) -> None:
        """When UPDATE touches 1 row, advance_saga must return True and insert an event."""
        conn = _make_mock_conn(rowcount=1)

        from app.db.saga import advance_saga

        result = await advance_saga(
            conn,
            saga_id=SAGA_ID,
            tenant_id=TENANT_ID,
            from_status="RECEIVED",
            to_status="SYNTHESIZING",
            version=0,
        )

        assert result is True
        # First call: UPDATE; second call: INSERT event
        assert conn.execute.await_count == 2

    async def test_advance_saga_returns_false_on_rowcount_0(self) -> None:
        """When UPDATE touches 0 rows (stale lock), advance_saga must return False.

        The event INSERT must NOT be called — optimistic lock miss is silent.
        """
        conn = _make_mock_conn(rowcount=0)

        from app.db.saga import advance_saga

        result = await advance_saga(
            conn,
            saga_id=SAGA_ID,
            tenant_id=TENANT_ID,
            from_status="RECEIVED",
            to_status="SYNTHESIZING",
            version=99,  # stale version
        )

        assert result is False
        # Only the UPDATE was executed; no event insert
        assert conn.execute.await_count == 1


class TestMarkIngestProcessed:
    """Unit tests for app.db.saga.mark_ingest_processed."""

    async def test_mark_ingest_processed_executes_update(self) -> None:
        """mark_ingest_processed must call conn.execute exactly once."""
        conn = _make_mock_conn()

        from app.db.saga import mark_ingest_processed

        await mark_ingest_processed(conn, INGEST_ID, TENANT_ID)

        conn.execute.assert_awaited_once()


# ===========================================================================
# INTEGRATION TESTS
# ===========================================================================


@pytest.mark.integration
@_SKIP_INTEGRATION
class TestCreateSagaIntegration:
    """S3 integration — create_saga against a live PostgreSQL instance.

    Prerequisites
    -------------
    * DATABASE_URL env var set to a valid connection string.
    * Migrations applied (feature_sagas, feature_saga_events, ingest_queue).
    * TEST_TENANT_ID must be a pre-existing tenant UUID in the ``tenants`` table.
    * A pre-existing row in ``ingest_queue`` whose id matches INTEGRATION_INGEST_ID.
    """

    INTEGRATION_TENANT_ID: str = os.environ.get(
        "TEST_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    INTEGRATION_INGEST_ID: str = os.environ.get(
        "TEST_INGEST_ID", "00000000-0000-0000-0000-000000000002"
    )

    async def test_create_saga_integration(self) -> None:
        """create_saga inserts a RECEIVED saga row and its initial event."""
        import psycopg  # noqa: PLC0415

        tenant_id = uuid.UUID(self.INTEGRATION_TENANT_ID)
        ingest_id = uuid.UUID(self.INTEGRATION_INGEST_ID)

        from app.db.saga import create_saga  # noqa: PLC0415

        saga_id: uuid.UUID | None = None
        try:
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(tenant_id),),
                    )
                    saga_id = await create_saga(conn, ingest_id, tenant_id)

            # Verify saga row
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                await conn.execute(
                    "SET LOCAL app.current_tenant = %s",
                    (str(tenant_id),),
                )
                cursor = await conn.execute(
                    "SELECT status, version FROM feature_sagas WHERE id = %s AND tenant_id = %s",
                    (str(saga_id), str(tenant_id)),
                )
                row = await cursor.fetchone()

            assert row is not None, "Expected saga row in feature_sagas"
            status, version = row
            assert status == "RECEIVED"
            assert version == 0

            # Verify initial event row
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                await conn.execute(
                    "SET LOCAL app.current_tenant = %s",
                    (str(tenant_id),),
                )
                cursor = await conn.execute(
                    """
                    SELECT from_status, to_status, actor
                      FROM feature_saga_events
                     WHERE saga_id = %s AND tenant_id = %s
                    """,
                    (str(saga_id), str(tenant_id)),
                )
                event_row = await cursor.fetchone()

            assert event_row is not None, "Expected event row in feature_saga_events"
            from_status, to_status, actor = event_row
            assert from_status is None
            assert to_status == "RECEIVED"
            assert actor == "system"

        finally:
            if saga_id is not None:
                async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                    await conn.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(tenant_id),),
                    )
                    await conn.execute(
                        "DELETE FROM feature_saga_events WHERE saga_id = %s",
                        (str(saga_id),),
                    )
                    await conn.execute(
                        "DELETE FROM feature_sagas WHERE id = %s",
                        (str(saga_id),),
                    )
                    await conn.commit()


@pytest.mark.integration
@_SKIP_INTEGRATION
class TestAdvanceSagaIntegration:
    """S3 integration — advance_saga stale-lock detection."""

    INTEGRATION_TENANT_ID: str = os.environ.get(
        "TEST_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    INTEGRATION_INGEST_ID: str = os.environ.get(
        "TEST_INGEST_ID", "00000000-0000-0000-0000-000000000002"
    )

    async def test_advance_saga_stale_lock_returns_false(self) -> None:
        """Passing a stale version number must return False (optimistic lock miss)."""
        import psycopg  # noqa: PLC0415

        tenant_id = uuid.UUID(self.INTEGRATION_TENANT_ID)
        ingest_id = uuid.UUID(self.INTEGRATION_INGEST_ID)

        from app.db.saga import advance_saga, create_saga  # noqa: PLC0415

        saga_id: uuid.UUID | None = None
        try:
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(tenant_id),),
                    )
                    saga_id = await create_saga(conn, ingest_id, tenant_id)

            # Now try to advance with a wrong (stale) version
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(tenant_id),),
                    )
                    result = await advance_saga(
                        conn,
                        saga_id=saga_id,
                        tenant_id=tenant_id,
                        from_status="RECEIVED",
                        to_status="SYNTHESIZING",
                        version=99,  # deliberately stale
                    )

            assert result is False, "Expected False for stale optimistic lock"

        finally:
            if saga_id is not None:
                async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                    await conn.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(tenant_id),),
                    )
                    await conn.execute(
                        "DELETE FROM feature_saga_events WHERE saga_id = %s",
                        (str(saga_id),),
                    )
                    await conn.execute(
                        "DELETE FROM feature_sagas WHERE id = %s",
                        (str(saga_id),),
                    )
                    await conn.commit()

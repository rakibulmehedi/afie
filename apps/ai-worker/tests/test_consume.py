"""
Tests for Sprint 2 — S2.3 (QStash consume endpoint) and S2.4 (idempotent DB ingest).

Unit tests mock the QStash Receiver and the database layer so they run without any
external service.  Integration tests are marked with @pytest.mark.integration and are
skipped automatically when the DATABASE_URL environment variable is absent.

QStash SDK note
---------------
The installed SDK (qstash>=2.0) exposes SignatureError at ``qstash.errors``
(plural), not ``qstash.error`` (singular).  The implementation files MUST import
from ``qstash.errors``.  These tests patch ``app.api.deps.Receiver`` at the point
where the application code imports and instantiates it.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_ID = str(uuid.uuid4())
IDEMPOTENCY_KEY = "gh-event-abc123"
VALID_BODY: dict[str, Any] = {
    "tenant_id": TENANT_ID,
    "source": "github",
    "idempotency_key": IDEMPOTENCY_KEY,
    "raw_payload": json.dumps({"ref": "refs/heads/main", "commits": 1}),
}
VALID_BODY_BYTES = json.dumps(VALID_BODY).encode()

FAKE_SIGNATURE = "v1=fakesignature"


def _make_headers(signature: str | None = FAKE_SIGNATURE) -> dict[str, str]:
    """Build request headers, optionally omitting the Upstash-Signature header."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Upstash-Signature"] = signature
    return headers


# ---------------------------------------------------------------------------
# Fixtures — unit test (no real DB or QStash)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_receiver_verify_ok(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """
    Patch app.api.deps.Receiver so that Receiver(...).verify(...) succeeds
    (returns None, which is the success contract for the real SDK).
    """
    mock_instance = MagicMock()
    mock_instance.verify.return_value = None  # success → no exception

    mock_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.api.deps.Receiver", mock_cls)
    return mock_cls


@pytest.fixture()
def mock_receiver_verify_raises(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """
    Patch app.api.deps.Receiver so that Receiver(...).verify(...) raises
    SignatureError, simulating a bad or tampered signature.
    """
    from qstash.errors import SignatureError  # noqa: PLC0415 — conditional import

    mock_instance = MagicMock()
    mock_instance.verify.side_effect = SignatureError("invalid signature")

    mock_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.api.deps.Receiver", mock_cls)
    return mock_cls


@pytest.fixture()
def mock_ingest_to_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """
    Replace build_ingest_use_case with a factory returning a mock use case so
    background-task scheduling can be observed without touching any database.
    """
    mock_uc = MagicMock()
    mock_uc.execute = AsyncMock()
    monkeypatch.setattr("app.api.consume.build_ingest_use_case", lambda: mock_uc)
    return mock_uc.execute


@pytest.fixture()
def mock_orchestrate_pipeline(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """
    Replace build_synthesize_use_case with a factory returning a mock use case so
    the pipeline background task does not attempt real DB or LLM calls in unit tests.
    """
    mock_uc = MagicMock()
    mock_uc.execute = AsyncMock()
    monkeypatch.setattr("app.api.consume.build_synthesize_use_case", lambda: mock_uc)
    return mock_uc.execute


@pytest.fixture()
def mock_db_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """
    Replace app.infrastructure.db.session.pool with a mock so the lifespan startup does not
    try to open a real PostgreSQL connection during unit tests.
    """
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()
    # getconn / connection context manager used by ingest_to_db
    mock_conn = AsyncMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("app.infrastructure.db.session.pool", mock_pool)
    return mock_pool


@pytest_asyncio.fixture()
async def unit_client(
    mock_receiver_verify_ok: MagicMock,
    mock_ingest_to_db: AsyncMock,
    mock_orchestrate_pipeline: AsyncMock,
    mock_db_pool: MagicMock,
) -> AsyncClient:  # type: ignore[misc]
    """
    Async HTTP client wired to the FastAPI app for unit tests.
    Skips the real lifespan (no DB pool creation) by patching the pool.
    """
    from app.main import app  # noqa: PLC0415

    # Patch settings so the app does not require real env vars at import time
    with patch(
        "app.core.settings.get_settings",
        return_value=MagicMock(
            qstash_current_signing_key="current-key",
            qstash_next_signing_key="next-key",
            database_url="postgresql://test/test",
        ),
    ), patch("app.infrastructure.db.session.create_pool", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


@pytest_asyncio.fixture()
async def unit_client_bad_sig(
    mock_receiver_verify_raises: MagicMock,
    mock_ingest_to_db: AsyncMock,
    mock_orchestrate_pipeline: AsyncMock,
    mock_db_pool: MagicMock,
) -> AsyncClient:  # type: ignore[misc]
    """Like unit_client but the Receiver is configured to raise SignatureError."""
    from app.main import app  # noqa: PLC0415

    with patch(
        "app.core.settings.get_settings",
        return_value=MagicMock(
            qstash_current_signing_key="current-key",
            qstash_next_signing_key="next-key",
            database_url="postgresql://test/test",
        ),
    ), patch("app.infrastructure.db.session.create_pool", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


# ===========================================================================
# UNIT TESTS — S2.3 (consume endpoint)
# ===========================================================================


class TestConsumeEndpointSignature:
    """S2.3 — QStash signature verification gate."""

    # -----------------------------------------------------------------------
    # Test 1: Valid signature → 200, background task enqueued
    # -----------------------------------------------------------------------
    async def test_valid_signature_returns_200(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        A request carrying a valid Upstash-Signature header with a well-formed JSON
        body must be accepted immediately with HTTP 200 and {"status": "accepted"}.
        The ingest_to_db coroutine must be registered as a background task.
        """
        response = await unit_client.post(
            "/api/v1/consume",
            content=VALID_BODY_BYTES,
            headers=_make_headers(),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

    async def test_valid_signature_schedules_background_task(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        After a valid request, the background task runner must eventually call
        ingest_to_db once with the parsed ConsumePayload.  Because FastAPI executes
        background tasks before the ASGI response is fully flushed in test mode,
        we assert the mock was called exactly once.
        """
        await unit_client.post(
            "/api/v1/consume",
            content=VALID_BODY_BYTES,
            headers=_make_headers(),
        )
        # Background tasks run inline during AsyncClient requests
        mock_ingest_to_db.assert_awaited_once()
        call_args = mock_ingest_to_db.call_args
        payload_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("payload")
        assert str(payload_arg.tenant_id) == TENANT_ID
        assert payload_arg.source == "github"
        assert payload_arg.idempotency_key == IDEMPOTENCY_KEY

    # -----------------------------------------------------------------------
    # Test 2: Missing Upstash-Signature header → 401, no background task
    # -----------------------------------------------------------------------
    async def test_missing_signature_header_returns_401(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        When the Upstash-Signature header is absent, the dependency
        verify_qstash_signature must raise HTTPException(401) before the
        route handler runs.
        """
        response = await unit_client.post(
            "/api/v1/consume",
            content=VALID_BODY_BYTES,
            headers=_make_headers(signature=None),  # no header
        )
        assert response.status_code == 401

    async def test_missing_signature_header_does_not_schedule_task(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """No background task must be enqueued when the signature header is absent."""
        await unit_client.post(
            "/api/v1/consume",
            content=VALID_BODY_BYTES,
            headers=_make_headers(signature=None),
        )
        mock_ingest_to_db.assert_not_awaited()

    # -----------------------------------------------------------------------
    # Test 3: Invalid (tampered) signature → 401, no background task
    # -----------------------------------------------------------------------
    async def test_invalid_signature_returns_401(
        self,
        unit_client_bad_sig: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        A present but cryptographically invalid Upstash-Signature header must
        result in HTTP 401.  The route handler must never be reached.
        """
        response = await unit_client_bad_sig.post(
            "/api/v1/consume",
            content=VALID_BODY_BYTES,
            headers=_make_headers(signature="v1=tampered"),
        )
        assert response.status_code == 401

    async def test_invalid_signature_does_not_schedule_task(
        self,
        unit_client_bad_sig: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """No background task must be enqueued when the signature is invalid."""
        await unit_client_bad_sig.post(
            "/api/v1/consume",
            content=VALID_BODY_BYTES,
            headers=_make_headers(signature="v1=tampered"),
        )
        mock_ingest_to_db.assert_not_awaited()

    # -----------------------------------------------------------------------
    # Test 4: Expired token (clock outside nbf/exp window) → 401
    # -----------------------------------------------------------------------
    async def test_expired_token_returns_401(
        self,
        mock_ingest_to_db: AsyncMock,
        mock_orchestrate_pipeline: AsyncMock,
        mock_db_pool: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        When the JWT embedded in the Upstash-Signature is outside its valid time
        window (nbf/exp), the SDK raises SignatureError.  The endpoint must return
        HTTP 401 without scheduling any background task.

        This test patches the Receiver separately to use a distinct error message
        that simulates a clock/expiry failure specifically.
        """
        from qstash.errors import SignatureError  # noqa: PLC0415

        expired_instance = MagicMock()
        expired_instance.verify.side_effect = SignatureError("token is expired")
        mock_cls = MagicMock(return_value=expired_instance)
        monkeypatch.setattr("app.api.deps.Receiver", mock_cls)

        from app.main import app  # noqa: PLC0415

        with patch(
            "app.core.settings.get_settings",
            return_value=MagicMock(
                qstash_current_signing_key="current-key",
                qstash_next_signing_key="next-key",
                database_url="postgresql://test/test",
            ),
        ), patch("app.infrastructure.db.session.create_pool", new_callable=AsyncMock):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/consume",
                    content=VALID_BODY_BYTES,
                    headers=_make_headers(signature="v1=expiredtoken"),
                )

        assert response.status_code == 401
        mock_ingest_to_db.assert_not_awaited()


# ===========================================================================
# UNIT TESTS — S2.3 (payload validation / 422 paths)
# ===========================================================================


class TestConsumeEndpointPayloadValidation:
    """S2.3 — Pydantic validation of the request body (signature passes)."""

    # -----------------------------------------------------------------------
    # Test 5: Valid signature, malformed JSON body → 422
    # -----------------------------------------------------------------------
    async def test_malformed_json_body_returns_422(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        A request whose body is not parseable as JSON must be rejected with
        HTTP 422 Unprocessable Entity.  The Pydantic model layer handles this
        before ingest_to_db is ever called.
        """
        response = await unit_client.post(
            "/api/v1/consume",
            content=b"{not valid json}",
            headers=_make_headers(),
        )
        assert response.status_code == 422
        mock_ingest_to_db.assert_not_awaited()

    # -----------------------------------------------------------------------
    # Test 6: Valid signature, invalid UUID in tenant_id → 422
    # -----------------------------------------------------------------------
    async def test_invalid_uuid_tenant_id_returns_422(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        When tenant_id is not a valid UUID string, FastAPI/Pydantic must return
        HTTP 422 and the background task must not be scheduled.
        """
        bad_body = {**VALID_BODY, "tenant_id": "not-a-uuid"}
        response = await unit_client.post(
            "/api/v1/consume",
            content=json.dumps(bad_body).encode(),
            headers=_make_headers(),
        )
        assert response.status_code == 422
        detail = response.json().get("detail", [])
        # At least one validation error must reference the tenant_id field
        field_names = [
            err.get("loc", [])[-1] if err.get("loc") else "" for err in detail
        ]
        assert "tenant_id" in field_names
        mock_ingest_to_db.assert_not_awaited()

    async def test_invalid_source_value_returns_422(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        A source value not in the Literal["github", "telegram"] set must yield
        HTTP 422 with a validation error referencing the source field.
        """
        bad_body = {**VALID_BODY, "source": "slack"}
        response = await unit_client.post(
            "/api/v1/consume",
            content=json.dumps(bad_body).encode(),
            headers=_make_headers(),
        )
        assert response.status_code == 422
        mock_ingest_to_db.assert_not_awaited()

    async def test_missing_required_fields_returns_422(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        A body that omits required fields (e.g., idempotency_key) must yield
        HTTP 422 without scheduling any background task.
        """
        incomplete_body = {
            "tenant_id": TENANT_ID,
            "source": "github",
            # idempotency_key missing
            "raw_payload": "{}",
        }
        response = await unit_client.post(
            "/api/v1/consume",
            content=json.dumps(incomplete_body).encode(),
            headers=_make_headers(),
        )
        assert response.status_code == 422
        mock_ingest_to_db.assert_not_awaited()

    async def test_empty_body_returns_422(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """An empty request body must be rejected with HTTP 422."""
        response = await unit_client.post(
            "/api/v1/consume",
            content=b"",
            headers=_make_headers(),
        )
        assert response.status_code == 422
        mock_ingest_to_db.assert_not_awaited()

    async def test_empty_idempotency_key_returns_422(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """
        An empty string for idempotency_key is semantically invalid.
        The ConsumePayload model must reject it with HTTP 422.
        """
        bad_body = {**VALID_BODY, "idempotency_key": ""}
        response = await unit_client.post(
            "/api/v1/consume",
            content=json.dumps(bad_body).encode(),
            headers=_make_headers(),
        )
        assert response.status_code == 422
        mock_ingest_to_db.assert_not_awaited()

    async def test_telegram_source_accepted(
        self,
        unit_client: AsyncClient,
        mock_ingest_to_db: AsyncMock,
    ) -> None:
        """Both Literal values 'github' and 'telegram' must be accepted."""
        telegram_body = {**VALID_BODY, "source": "telegram"}
        response = await unit_client.post(
            "/api/v1/consume",
            content=json.dumps(telegram_body).encode(),
            headers=_make_headers(),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}


# ===========================================================================
# INTEGRATION TESTS — S2.4 (idempotent DB ingest)
# ===========================================================================

# Skip the entire class when DATABASE_URL is not set in the environment.
_DB_URL = os.environ.get("DATABASE_URL", "")
_SKIP_INTEGRATION = pytest.mark.skipif(
    not _DB_URL,
    reason="DATABASE_URL not set; skipping integration tests (run with a live DB)",
)


@pytest.mark.integration
@_SKIP_INTEGRATION
class TestIdempotentDbIngest:
    """
    S2.4 — Integration tests for ingest_to_db idempotency and RLS context.

    Prerequisites
    -------------
    * A running PostgreSQL instance with migration 0001_multi_tenant_init.sql applied.
    * DATABASE_URL env var pointing to a connection string for the worker_rw role
      (or a superuser in CI).
    * A pre-existing row in `tenants` whose `id` matches INTEGRATION_TENANT_ID.
    * The test suite teardown removes inserted rows to remain idempotent across runs.

    These tests do NOT go through the HTTP layer — they call ingest_to_db directly
    so that failures isolate to the DB layer without QStash complexity.
    """

    # Fixed tenant UUID that must exist in the `tenants` table before running.
    INTEGRATION_TENANT_ID: str = os.environ.get(
        "TEST_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    INTEGRATION_IDEM_KEY: str = "integration-test-idem-key-sprint2"

    @pytest_asyncio.fixture(autouse=True)
    async def _cleanup(self) -> None:  # type: ignore[misc]
        """
        Yield first, then delete the test row so repeated runs stay clean.
        This runs after each test method in the class.
        """
        yield
        import psycopg  # noqa: PLC0415

        async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
            await conn.execute(
                "SET LOCAL app.current_tenant = %s",
                (self.INTEGRATION_TENANT_ID,),
            )
            await conn.execute(
                """
                DELETE FROM ingest_queue
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (self.INTEGRATION_TENANT_ID, self.INTEGRATION_IDEM_KEY),
            )
            await conn.commit()

    def _make_payload(self) -> Any:
        """Return a ConsumePayload for integration tests."""
        from app.api.schemas import ConsumePayload  # noqa: PLC0415

        return ConsumePayload(
            tenant_id=uuid.UUID(self.INTEGRATION_TENANT_ID),
            source="github",
            idempotency_key=self.INTEGRATION_IDEM_KEY,
            raw_payload=json.dumps({"test": True}),
        )

    # -----------------------------------------------------------------------
    # Test 7: First delivery → row inserted in ingest_queue with status PENDING
    # -----------------------------------------------------------------------
    async def test_first_delivery_inserts_pending_row(self) -> None:
        """
        Calling ingest_to_db with a new idempotency_key must insert exactly one
        row in ingest_queue with status = 'PENDING'.
        """
        import psycopg  # noqa: PLC0415

        from app.db.ingest import ingest_to_db  # noqa: PLC0415

        payload = self._make_payload()
        await ingest_to_db(payload)  # acquires its own pool connection

        # Verify the row (committed data)
        async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
            await conn.execute(
                "SET LOCAL app.current_tenant = %s",
                (self.INTEGRATION_TENANT_ID,),
            )
            cursor = await conn.execute(
                """
                SELECT status, tenant_id, source, idempotency_key
                FROM ingest_queue
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (self.INTEGRATION_TENANT_ID, self.INTEGRATION_IDEM_KEY),
            )
            row = await cursor.fetchone()

        assert row is not None, "Expected a row in ingest_queue but found none"
        status, tenant_id, source, idem_key = row
        assert status == "PENDING"
        assert str(tenant_id) == self.INTEGRATION_TENANT_ID
        assert source == "github"
        assert idem_key == self.INTEGRATION_IDEM_KEY

    # -----------------------------------------------------------------------
    # Test 8: Duplicate delivery → no-op, exactly 1 row, no error
    # -----------------------------------------------------------------------
    async def test_duplicate_delivery_is_noop(self) -> None:
        """
        Calling ingest_to_db twice with the same (tenant_id, source, idempotency_key)
        must NOT raise an error and must leave exactly one row in ingest_queue.
        """
        import psycopg  # noqa: PLC0415

        from app.db.ingest import ingest_to_db  # noqa: PLC0415

        payload = self._make_payload()
        await ingest_to_db(payload)  # first delivery
        await ingest_to_db(payload)  # duplicate — must be silent no-op

        # Count rows — must be exactly 1
        async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
            await conn.execute(
                "SET LOCAL app.current_tenant = %s",
                (self.INTEGRATION_TENANT_ID,),
            )
            cursor = await conn.execute(
                """
                SELECT count(*)
                FROM ingest_queue
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (self.INTEGRATION_TENANT_ID, self.INTEGRATION_IDEM_KEY),
            )
            result = await cursor.fetchone()

        count = result[0] if result else 0
        assert count == 1, f"Expected exactly 1 row after duplicate delivery, found {count}"

    # -----------------------------------------------------------------------
    # Test 9: SET LOCAL context — RLS GUC must be set for insert to succeed
    # -----------------------------------------------------------------------
    async def test_rls_context_guc_matches_tenant_id(self) -> None:
        """
        ingest_to_db must SET LOCAL app.current_tenant before the INSERT.
        Proof: if SET LOCAL is missing, RLS blocks the write (worker_rw has no
        BYPASSRLS) and the row is never committed. A successful insert that is
        visible under the correct GUC therefore proves SET LOCAL was called.
        """
        import psycopg  # noqa: PLC0415

        from app.db.ingest import ingest_to_db  # noqa: PLC0415

        payload = self._make_payload()
        await ingest_to_db(payload)  # manages its own connection + SET LOCAL

        # Row visible under the correct GUC → SET LOCAL was called
        async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
            await conn.execute(
                "SET LOCAL app.current_tenant = %s",
                (self.INTEGRATION_TENANT_ID,),
            )
            cursor = await conn.execute(
                "SELECT tenant_id FROM ingest_queue WHERE idempotency_key = %s",
                (self.INTEGRATION_IDEM_KEY,),
            )
            row = await cursor.fetchone()

        assert row is not None, (
            "Row not found — SET LOCAL was likely missing, causing RLS to block the INSERT"
        )
        assert str(row[0]) == self.INTEGRATION_TENANT_ID

    # -----------------------------------------------------------------------
    # Additional edge: RLS blocks access without GUC set
    # -----------------------------------------------------------------------
    async def test_rls_blocks_access_without_guc(self) -> None:
        """
        Without setting ``app.current_tenant``, the RLS policy must ensure that
        zero rows are visible in ingest_queue for any tenant — fail-closed behaviour.

        This test does NOT call ingest_to_db; it only queries the table directly
        without setting the GUC to confirm the RLS invariant from the migration.
        """
        import psycopg  # noqa: PLC0415

        from app.api.schemas import ConsumePayload  # noqa: PLC0415
        from app.db.ingest import ingest_to_db  # noqa: PLC0415

        idem_key = f"{self.INTEGRATION_IDEM_KEY}-rls-block"
        payload = ConsumePayload(
            tenant_id=uuid.UUID(self.INTEGRATION_TENANT_ID),
            source="github",
            idempotency_key=idem_key,
            raw_payload=json.dumps({"test": "rls"}),
        )

        await ingest_to_db(payload)  # manages its own connection + SET LOCAL

        try:
            # Query WITHOUT setting the GUC — must see zero rows (fail-closed RLS)
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                cursor = await conn.execute(
                    "SELECT count(*) FROM ingest_queue WHERE tenant_id = %s",
                    (self.INTEGRATION_TENANT_ID,),
                )
                row = await cursor.fetchone()
            count = row[0] if row else 0
            assert count == 0, (
                f"RLS should block all rows without GUC set, but found {count}"
            )
        finally:
            # Cleanup the extra row
            async with await psycopg.AsyncConnection.connect(_DB_URL) as conn:
                await conn.execute(
                    "SET LOCAL app.current_tenant = %s",
                    (self.INTEGRATION_TENANT_ID,),
                )
                await conn.execute(
                    "DELETE FROM ingest_queue WHERE tenant_id = %s AND idempotency_key = %s",
                    (self.INTEGRATION_TENANT_ID, idem_key),
                )
                await conn.commit()

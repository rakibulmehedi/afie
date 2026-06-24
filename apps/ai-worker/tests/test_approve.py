"""
Tests for the POST /api/v1/approve endpoint.

Unit tests mock the QStash Receiver and the database layer so they run without any
external service.  Follows the same patterns as test_consume.py.

QStash SDK note
---------------
These tests patch ``app.api.deps.Receiver`` at the point where the application
code imports and instantiates it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

TENANT_ID = str(uuid.uuid4())
DRAFT_ID = str(uuid.uuid4())
SAGA_ID = str(uuid.uuid4())

VALID_REJECT_BODY: dict[str, Any] = {
    "draft_id": DRAFT_ID,
    "tenant_id": TENANT_ID,
    "decision": "REJECT",
    "actor": "dashboard",
}

VALID_APPROVE_BODY: dict[str, Any] = {
    "draft_id": DRAFT_ID,
    "tenant_id": TENANT_ID,
    "decision": "APPROVE",
    "actor": "dashboard",
}

VALID_APPROVE_EDITED_BODY: dict[str, Any] = {
    "draft_id": DRAFT_ID,
    "tenant_id": TENANT_ID,
    "decision": "APPROVE",
    "edited_content": "This is the edited content.",
    "actor": "dashboard",
}

FAKE_SIGNATURE = "v1=fakesignature"

_MOCK_SETTINGS = MagicMock(
    qstash_current_signing_key="current-key",
    qstash_next_signing_key="next-key",
    database_url="postgresql://test/test",
    qstash_distribution_topic="https://qstash.example.com/distribution",
    qstash_token="dummy-token",
)

_MOCK_SETTINGS_NO_TOPIC = MagicMock(
    qstash_current_signing_key="current-key",
    qstash_next_signing_key="next-key",
    database_url="postgresql://test/test",
    qstash_distribution_topic="",  # empty → dry-run
    qstash_token="dummy-token",
)


def _make_headers(signature: str | None = FAKE_SIGNATURE) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Upstash-Signature"] = signature
    return headers


def _make_mock_conn(
    draft_row: tuple | None = None,
    saga_row: tuple | None = None,
    approve_returning_row: tuple | None = None,
    saga_update_rowcount: int = 1,
    draft_update_rowcount: int = 1,
) -> AsyncMock:
    """Build a mock psycopg AsyncConnection that returns canned query results.

    The mock tracks execute() calls and returns different cursors based on
    which query is being run (detected by keyword presence).
    """
    if draft_row is None:
        draft_row = (uuid.UUID(DRAFT_ID), uuid.UUID(SAGA_ID), "AWAITING_APPROVAL")
    if saga_row is None:
        saga_row = (uuid.UUID(SAGA_ID), "AWAITING_APPROVAL", 3)
    if approve_returning_row is None:
        approve_returning_row = (
            uuid.UUID(SAGA_ID),
            "linkedin",
            "tok_abc123",
            uuid.UUID(TENANT_ID),
        )

    conn = AsyncMock()

    call_index = [0]

    async def _execute(sql: str, params: Any = None) -> MagicMock:
        """Return different cursors based on the SQL content."""
        cursor = MagicMock()
        sql_stripped = sql.strip()

        if "SET LOCAL" in sql_stripped:
            cursor.fetchone = AsyncMock(return_value=None)
            cursor.rowcount = 0
        elif "cspe_drafts" in sql_stripped and "FOR UPDATE" in sql_stripped:
            # SELECT from cspe_drafts FOR UPDATE (lock draft)
            cursor.fetchone = AsyncMock(return_value=draft_row)
            cursor.rowcount = 1
        elif "feature_sagas" in sql_stripped and "FOR UPDATE" in sql_stripped:
            # SELECT from feature_sagas FOR UPDATE (lock saga)
            cursor.fetchone = AsyncMock(return_value=saga_row)
            cursor.rowcount = 1
        elif "cspe_drafts" in sql_stripped and "UPDATE" in sql_stripped and "RETURNING" in sql_stripped:
            # UPDATE cspe_drafts RETURNING ... (approve path)
            cursor.fetchone = AsyncMock(return_value=approve_returning_row)
            cursor.rowcount = draft_update_rowcount
        elif "cspe_drafts" in sql_stripped and "UPDATE" in sql_stripped:
            # UPDATE cspe_drafts without RETURNING (reject path)
            cursor.fetchone = AsyncMock(return_value=None)
            cursor.rowcount = draft_update_rowcount
        elif "feature_sagas" in sql_stripped and "UPDATE" in sql_stripped:
            # UPDATE feature_sagas (optimistic lock advance)
            cursor.fetchone = AsyncMock(return_value=None)
            cursor.rowcount = saga_update_rowcount
        elif "feature_saga_events" in sql_stripped:
            # INSERT into feature_saga_events
            cursor.fetchone = AsyncMock(return_value=None)
            cursor.rowcount = 1
        else:
            cursor.fetchone = AsyncMock(return_value=None)
            cursor.rowcount = 0

        return cursor

    conn.execute = _execute

    # Support async context manager for transaction()
    tx_cm = AsyncMock()
    tx_cm.__aenter__ = AsyncMock(return_value=None)
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_cm)

    return conn


def _make_pool_with_conn(conn: AsyncMock) -> MagicMock:
    """Wrap a mock connection in a mock pool."""
    pool = MagicMock()
    pool.close = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ---------------------------------------------------------------------------
# Fixtures — shared
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_receiver_verify_ok(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_instance = MagicMock()
    mock_instance.verify.return_value = None
    mock_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.api.deps.Receiver", mock_cls)
    return mock_cls


@pytest.fixture()
def mock_receiver_verify_raises(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    from qstash.errors import SignatureError

    mock_instance = MagicMock()
    mock_instance.verify.side_effect = SignatureError("invalid signature")
    mock_cls = MagicMock(return_value=mock_instance)
    monkeypatch.setattr("app.api.deps.Receiver", mock_cls)
    return mock_cls


@pytest.fixture()
def mock_db_pool_with_conn(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, AsyncMock]:
    """Patch app.infrastructure.db.session.pool with a mock that has a working connection."""
    conn = _make_mock_conn()
    pool = _make_pool_with_conn(conn)
    monkeypatch.setattr("app.infrastructure.db.session.pool", pool)
    return pool, conn


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestApproveEndpoint:
    """Unit tests for POST /api/v1/approve."""

    # ------------------------------------------------------------------
    # Test 1: Missing Upstash-Signature → 401
    # ------------------------------------------------------------------
    async def test_missing_signature_returns_401(
        self,
        mock_receiver_verify_ok: MagicMock,
        mock_db_pool_with_conn: tuple[MagicMock, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A request with no Upstash-Signature header must return HTTP 401."""
        from app.main import app

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_REJECT_BODY).encode(),
                    headers=_make_headers(signature=None),
                )
        assert response.status_code == 401

    # ------------------------------------------------------------------
    # Test 2: Invalid signature → 401
    # ------------------------------------------------------------------
    async def test_invalid_signature_returns_401(
        self,
        mock_receiver_verify_raises: MagicMock,
        mock_db_pool_with_conn: tuple[MagicMock, AsyncMock],
    ) -> None:
        """A present but cryptographically invalid signature must return HTTP 401."""
        from app.main import app

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_REJECT_BODY).encode(),
                    headers=_make_headers(signature="v1=tampered"),
                )
        assert response.status_code == 401

    # ------------------------------------------------------------------
    # Test 3: Missing draft_id → 422
    # ------------------------------------------------------------------
    async def test_missing_draft_id_returns_422(
        self,
        mock_receiver_verify_ok: MagicMock,
        mock_db_pool_with_conn: tuple[MagicMock, AsyncMock],
    ) -> None:
        """A body missing draft_id must return HTTP 422."""
        from app.main import app

        body = {k: v for k, v in VALID_REJECT_BODY.items() if k != "draft_id"}
        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(body).encode(),
                    headers=_make_headers(),
                )
        assert response.status_code == 422

    # ------------------------------------------------------------------
    # Test 4: Missing tenant_id → 422
    # ------------------------------------------------------------------
    async def test_missing_tenant_id_returns_422(
        self,
        mock_receiver_verify_ok: MagicMock,
        mock_db_pool_with_conn: tuple[MagicMock, AsyncMock],
    ) -> None:
        """A body missing tenant_id must return HTTP 422."""
        from app.main import app

        body = {k: v for k, v in VALID_REJECT_BODY.items() if k != "tenant_id"}
        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(body).encode(),
                    headers=_make_headers(),
                )
        assert response.status_code == 422

    # ------------------------------------------------------------------
    # Test 5: Invalid decision value → 422
    # ------------------------------------------------------------------
    async def test_invalid_decision_returns_422(
        self,
        mock_receiver_verify_ok: MagicMock,
        mock_db_pool_with_conn: tuple[MagicMock, AsyncMock],
    ) -> None:
        """A decision value outside Literal['APPROVE','REJECT'] must return HTTP 422."""
        from app.main import app

        body = {**VALID_REJECT_BODY, "decision": "MAYBE"}
        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(body).encode(),
                    headers=_make_headers(),
                )
        assert response.status_code == 422

    # ------------------------------------------------------------------
    # Test 6: REJECT with valid payload → 200, DB updates called
    # ------------------------------------------------------------------
    async def test_reject_valid_payload_returns_200(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A valid REJECT payload must return 200 and call DB updates."""
        from app.main import app

        # Capture SQL calls via the mock connection
        conn = _make_mock_conn(saga_update_rowcount=1)
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        executed_sqls: list[str] = []
        original_execute = conn.execute

        async def tracking_execute(sql: str, params: Any = None) -> MagicMock:
            executed_sqls.append(sql.strip())
            return await original_execute(sql, params)

        conn.execute = tracking_execute

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_REJECT_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

        # Verify that SQL updates were executed for both the draft and saga tables
        update_sqls = [s for s in executed_sqls if "UPDATE" in s]
        assert any("cspe_drafts" in s for s in update_sqls), "Expected UPDATE on cspe_drafts"
        assert any("feature_sagas" in s for s in update_sqls), "Expected UPDATE on feature_sagas"

        # Verify an event was inserted
        event_sqls = [s for s in executed_sqls if "feature_saga_events" in s and "INSERT" in s]
        assert event_sqls, "Expected INSERT into feature_saga_events"

    # ------------------------------------------------------------------
    # Test 7: REJECT with optimistic lock conflict → 409
    # ------------------------------------------------------------------
    async def test_reject_optimistic_lock_conflict_returns_409(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When saga UPDATE rowcount=0 (lock conflict), endpoint must return 409."""
        from app.main import app

        conn = _make_mock_conn(saga_update_rowcount=0)  # simulate lock conflict
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_REJECT_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 409

    # ------------------------------------------------------------------
    # Test 8: APPROVE without edited_content → 200, approval_status='APPROVED'
    # ------------------------------------------------------------------
    async def test_approve_without_edited_content_returns_200(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A valid APPROVE (no edited_content) must return 200."""
        from app.main import app

        conn = _make_mock_conn(saga_update_rowcount=1)
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        executed_sqls: list[str] = []
        executed_params: list[Any] = []
        original_execute = conn.execute

        async def tracking_execute(sql: str, params: Any = None) -> MagicMock:
            executed_sqls.append(sql.strip())
            executed_params.append(params)
            return await original_execute(sql, params)

        conn.execute = tracking_execute

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ), patch("app.api.approve._publish_distribution", new_callable=AsyncMock):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_APPROVE_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

        # Verify approval_status='APPROVED' was used (no edited_content in body)
        approve_update_params = [
            p for sql, p in zip(executed_sqls, executed_params)
            if "cspe_drafts" in sql and "UPDATE" in sql and "RETURNING" in sql
        ]
        assert approve_update_params, "Expected UPDATE cspe_drafts RETURNING"
        # First param in the UPDATE is approval_status
        assert approve_update_params[0][0] == "APPROVED"
        # Second param (edited_content) must be None
        assert approve_update_params[0][1] is None

    # ------------------------------------------------------------------
    # Test 9: APPROVE with edited_content → 200, approval_status='EDITED'
    # ------------------------------------------------------------------
    async def test_approve_with_edited_content_returns_200_edited(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A valid APPROVE with edited_content must use approval_status='EDITED'."""
        from app.main import app

        conn = _make_mock_conn(saga_update_rowcount=1)
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        executed_sqls: list[str] = []
        executed_params: list[Any] = []
        original_execute = conn.execute

        async def tracking_execute(sql: str, params: Any = None) -> MagicMock:
            executed_sqls.append(sql.strip())
            executed_params.append(params)
            return await original_execute(sql, params)

        conn.execute = tracking_execute

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ), patch("app.api.approve._publish_distribution", new_callable=AsyncMock):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_APPROVE_EDITED_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

        # Verify approval_status='EDITED' was used
        approve_update_params = [
            p for sql, p in zip(executed_sqls, executed_params)
            if "cspe_drafts" in sql and "UPDATE" in sql and "RETURNING" in sql
        ]
        assert approve_update_params, "Expected UPDATE cspe_drafts RETURNING"
        assert approve_update_params[0][0] == "EDITED"
        assert approve_update_params[0][1] == "This is the edited content."

    # ------------------------------------------------------------------
    # Test 10: APPROVE → QStash distribution published with correct payload
    # ------------------------------------------------------------------
    async def test_approve_publishes_qstash_distribution(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After APPROVE commit, _publish_distribution must be called with correct args."""
        from app.main import app

        conn = _make_mock_conn(saga_update_rowcount=1)
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        mock_publish = AsyncMock()
        monkeypatch.setattr("app.api.approve._publish_distribution", mock_publish)

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_APPROVE_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 200
        mock_publish.assert_awaited_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["saga_id"] == SAGA_ID
        assert call_kwargs["draft_id"] == DRAFT_ID
        # platform and posting_token come from the mock returning row
        assert call_kwargs["platform"] == "linkedin"
        assert call_kwargs["posting_token"] == "tok_abc123"

    # ------------------------------------------------------------------
    # Test 11: APPROVE with empty qstash_distribution_topic → skip publish
    # ------------------------------------------------------------------
    async def test_approve_skips_publish_when_topic_empty(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When qstash_distribution_topic is empty, publish must be skipped."""
        from app.main import app

        conn = _make_mock_conn(saga_update_rowcount=1)
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        # Patch AsyncQStash to ensure it is NOT called
        mock_async_qstash_cls = MagicMock()
        mock_async_qstash_instance = AsyncMock()
        mock_async_qstash_cls.return_value = mock_async_qstash_instance
        monkeypatch.setattr("app.api.approve.AsyncQStash", mock_async_qstash_cls, raising=False)

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS_NO_TOPIC), patch(
            "app.api.approve.get_settings", return_value=_MOCK_SETTINGS_NO_TOPIC
        ), patch("app.infrastructure.db.session.create_pool", new_callable=AsyncMock):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_APPROVE_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 200
        # AsyncQStash should not have been instantiated since we short-circuit early
        mock_async_qstash_cls.assert_not_called()

    # ------------------------------------------------------------------
    # Test 12: DB exception during tx → 500
    # ------------------------------------------------------------------
    async def test_db_exception_returns_500(
        self,
        mock_receiver_verify_ok: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unexpected DB exception during processing must propagate as HTTP 500."""
        from app.main import app

        conn = AsyncMock()
        pool = _make_pool_with_conn(conn)
        monkeypatch.setattr("app.infrastructure.db.session.pool", pool)

        # Make execute() raise a generic database error
        conn.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        # transaction() still needs to work as a context manager
        tx_cm = AsyncMock()
        tx_cm.__aenter__ = AsyncMock(return_value=None)
        tx_cm.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx_cm)

        with patch("app.core.settings.get_settings", return_value=_MOCK_SETTINGS), patch(
            "app.infrastructure.db.session.create_pool", new_callable=AsyncMock
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/approve",
                    content=json.dumps(VALID_REJECT_BODY).encode(),
                    headers=_make_headers(),
                )

        assert response.status_code == 500

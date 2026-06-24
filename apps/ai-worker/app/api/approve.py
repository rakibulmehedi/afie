from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.deps import verify_qstash_signature
from app.api.schemas import ApprovePayload
from app.core.settings import get_settings
from app.db.session import get_conn

logger = logging.getLogger(__name__)

router = APIRouter()

__all__ = ["ApprovePayload", "router"]


async def _process_approve(payload: ApprovePayload) -> None:
    """Execute the approval/rejection logic in a single DB transaction.

    Decision flow:
    - REJECT: marks draft as REJECTED, transitions saga AWAITING_APPROVAL → REJECTED
    - APPROVE: marks draft as APPROVED/EDITED, transitions saga AWAITING_APPROVAL → APPROVED,
               then publishes a QStash distribution message after the transaction commits.

    The DB trigger ``trg_saga_touch`` auto-increments ``version`` on every UPDATE to
    ``feature_sagas``.  To avoid double-incrementing, we do NOT set ``version = version + 1``
    in our SQL — the trigger handles it.  We still use the current version value in the
    WHERE clause for optimistic locking.
    """
    async for conn in get_conn():
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL app.current_tenant = %s",
                (str(payload.tenant_id),),
            )

            # Lock and fetch the draft row
            draft_cursor = await conn.execute(
                """
                SELECT id, saga_id, approval_status
                  FROM cspe_drafts
                 WHERE id = %s
                   FOR UPDATE
                """,
                (str(payload.draft_id),),
            )
            draft_row = await draft_cursor.fetchone()
            if draft_row is None:
                raise HTTPException(status_code=404, detail="Draft not found")

            _, saga_id, _ = draft_row

            # Lock and fetch the saga row
            saga_cursor = await conn.execute(
                """
                SELECT id, status, version
                  FROM feature_sagas
                 WHERE id = %s
                   FOR UPDATE
                """,
                (str(saga_id),),
            )
            saga_row = await saga_cursor.fetchone()
            if saga_row is None:
                raise HTTPException(status_code=404, detail="Saga not found")

            _, saga_status, saga_version = saga_row

            # Initialized here so they're always bound; set in APPROVE branch below.
            target_platform: object = None
            posting_token: object = None

            if payload.decision == "REJECT":
                await conn.execute(
                    """
                    UPDATE cspe_drafts
                       SET approval_status = 'REJECTED',
                           decided_at = now()
                     WHERE id = %s
                    """,
                    (str(payload.draft_id),),
                )

                # Optimistic-lock transition: AWAITING_APPROVAL → REJECTED
                # We rely on trg_saga_touch to increment version; do NOT add version+1 here.
                update_cursor = await conn.execute(
                    """
                    UPDATE feature_sagas
                       SET status     = 'REJECTED',
                           updated_at = now()
                     WHERE id        = %s
                       AND tenant_id = %s
                       AND version   = %s
                       AND status    = 'AWAITING_APPROVAL'
                    """,
                    (str(saga_id), str(payload.tenant_id), saga_version),
                )

                if update_cursor.rowcount == 0:
                    # Optimistic lock conflict — another worker already advanced the saga
                    logger.warning(
                        "Optimistic lock conflict on saga %s (version=%s, status=%s) "
                        "during REJECT; skipping saga advance.",
                        saga_id,
                        saga_version,
                        saga_status,
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="Saga version conflict — transition already applied or status changed",
                    )

                # Record the transition event with the actor from payload
                await conn.execute(
                    """
                    INSERT INTO feature_saga_events
                                (saga_id, tenant_id, from_status, to_status, actor)
                    VALUES (%s, %s, 'AWAITING_APPROVAL', 'REJECTED', %s)
                    """,
                    (str(saga_id), str(payload.tenant_id), payload.actor),
                )

            else:
                # decision == "APPROVE"
                approval_status = "EDITED" if payload.edited_content is not None else "APPROVED"

                approve_cursor = await conn.execute(
                    """
                    UPDATE cspe_drafts
                       SET approval_status = %s,
                           decided_at      = now(),
                           edited_content  = %s
                     WHERE id = %s
                    RETURNING saga_id, target_platform, posting_token, tenant_id
                    """,
                    (approval_status, payload.edited_content, str(payload.draft_id)),
                )

                returning_row = await approve_cursor.fetchone()
                if returning_row is None:
                    raise HTTPException(status_code=404, detail="Draft not found on APPROVE update")

                _, target_platform, posting_token, _ = returning_row

                # Optimistic-lock transition: AWAITING_APPROVAL → APPROVED
                # We rely on trg_saga_touch to increment version; do NOT add version+1 here.
                update_cursor = await conn.execute(
                    """
                    UPDATE feature_sagas
                       SET status     = 'APPROVED',
                           updated_at = now()
                     WHERE id        = %s
                       AND tenant_id = %s
                       AND version   = %s
                       AND status    = 'AWAITING_APPROVAL'
                    """,
                    (str(saga_id), str(payload.tenant_id), saga_version),
                )

                if update_cursor.rowcount == 0:
                    logger.warning(
                        "Optimistic lock conflict on saga %s (version=%s, status=%s) "
                        "during APPROVE; skipping saga advance.",
                        saga_id,
                        saga_version,
                        saga_status,
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="Saga version conflict — transition already applied or status changed",
                    )

                # Record the transition event with the actor from payload
                await conn.execute(
                    """
                    INSERT INTO feature_saga_events
                                (saga_id, tenant_id, from_status, to_status, actor)
                    VALUES (%s, %s, 'AWAITING_APPROVAL', 'APPROVED', %s)
                    """,
                    (str(saga_id), str(payload.tenant_id), payload.actor),
                )

        # Transaction committed — now publish QStash if APPROVE
        if payload.decision == "APPROVE":
            await _publish_distribution(
                saga_id=str(saga_id),
                draft_id=str(payload.draft_id),
                platform=str(target_platform),
                posting_token=str(posting_token),
            )

        break  # Only one connection needed; exit the async generator


async def _publish_distribution(
    saga_id: str,
    draft_id: str,
    platform: str,
    posting_token: str,
) -> None:
    """Publish a distribution message to QStash after a successful APPROVE commit.

    If ``qstash_distribution_topic`` is empty, logs a warning and skips publish
    (dry-run mode for local / test environments).
    """
    s = get_settings()

    if not s.qstash_distribution_topic:
        logger.warning(
            "qstash_distribution_topic is not configured; skipping distribution publish "
            "for saga_id=%s draft_id=%s",
            saga_id,
            draft_id,
        )
        return

    from qstash.asyncio.client import AsyncQStash  # noqa: PLC0415 — lazy import

    client = AsyncQStash(token=s.qstash_token)
    await client.message.publish_json(
        url=s.qstash_distribution_topic,
        body={
            "saga_id": saga_id,
            "draft_id": draft_id,
            "platform": platform,
            "posting_token": posting_token,
        },
    )
    logger.info(
        "Published distribution message for saga_id=%s draft_id=%s to %s",
        saga_id,
        draft_id,
        s.qstash_distribution_topic,
    )


@router.post("/approve")
async def approve(
    body_bytes: bytes = Depends(verify_qstash_signature),
) -> dict[str, str]:
    """Human-approval webhook endpoint.

    Verifies the QStash signature, deserialises the ApprovePayload, and
    synchronously processes the approval or rejection decision against the
    database.  Returns HTTP 200 immediately on success so QStash does not retry.

    Processing is synchronous (not background) because the DB operations are
    fast (no LLM calls) and we need to surface DB errors back to the caller.
    """
    try:
        payload = ApprovePayload.model_validate_json(body_bytes)
    except (ValidationError, ValueError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        raise RequestValidationError(errors=errors)

    try:
        await _process_approve(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error processing approval for draft_id=%s", payload.draft_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    return {"status": "accepted"}

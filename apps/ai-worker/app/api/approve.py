from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.deps import verify_qstash_signature
from app.api.schemas import ApprovePayload
from app.core.settings import get_settings
from app.infrastructure.db.session import get_conn

logger = logging.getLogger(__name__)

router = APIRouter()

__all__ = ["ApprovePayload", "router"]


async def _process_approve(payload: ApprovePayload) -> None:
    """Delegate DB logic to ApproveUseCase; publish QStash after commit if APPROVE."""
    from app.core.container import build_approve_use_case  # noqa: PLC0415

    approve_uc = build_approve_use_case()
    saga_id_out: object = None
    target_platform: object = None
    posting_token: object = None

    async for conn in get_conn():
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL app.current_tenant = %s",
                (str(payload.tenant_id),),
            )
            saga_id_out, target_platform, posting_token = await approve_uc.execute(conn, payload)

        if payload.decision == "APPROVE":
            await _publish_distribution(
                saga_id=str(saga_id_out),
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

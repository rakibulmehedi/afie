from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.deps import verify_qstash_signature
from app.api.schemas import ConsumePayload
from app.core.container import build_ingest_use_case, build_synthesize_use_case

router = APIRouter()

__all__ = ["ConsumePayload", "router"]


@router.post("/consume")
async def consume(
    background_tasks: BackgroundTasks,
    body_bytes: bytes = Depends(verify_qstash_signature),
) -> dict[str, str]:
    """Fast-ack QStash webhook endpoint.

    Verifies the QStash signature (via dependency), deserializes the
    payload, enqueues background processing, and immediately returns
    HTTP 200 so QStash does not retry.

    All blocking I/O happens inside use-case execute methods, which run in the
    background after the response is sent. Every handler must be
    idempotent to satisfy at-least-once delivery guarantees.
    """
    try:
        payload = ConsumePayload.model_validate_json(body_bytes)
    except (ValidationError, ValueError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        raise RequestValidationError(errors=errors)
    ingest_uc = build_ingest_use_case()
    synth_uc = build_synthesize_use_case()
    background_tasks.add_task(ingest_uc.execute, payload)
    background_tasks.add_task(synth_uc.execute, payload)
    return {"status": "accepted"}

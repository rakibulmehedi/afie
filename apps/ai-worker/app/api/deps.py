from fastapi import HTTPException, Request
from qstash import Receiver

import app.core.settings as _core_settings


async def verify_qstash_signature(request: Request) -> bytes:
    """FastAPI dependency that verifies the QStash webhook signature.

    Reads the raw request body once and verifies the Upstash-Signature
    header using both the current and next signing keys. Returns the
    raw body bytes so the route can deserialize exactly once.

    Raises HTTPException(401) if the signature header is missing or invalid.

    IMPORTANT: Do NOT pass a fixed clock/timestamp to receiver.verify().
    The live clock is used by default. Passing a fixed clock makes
    signatures replayable (api_contracts.md §5.3.4).
    """
    body: bytes = await request.body()

    sig = request.headers.get("Upstash-Signature")
    if not sig:
        raise HTTPException(status_code=401, detail="Missing signature")

    s = _core_settings.get_settings()
    receiver = Receiver(
        current_signing_key=s.qstash_current_signing_key,
        next_signing_key=s.qstash_next_signing_key,
    )

    try:
        receiver.verify(body=body.decode(), signature=sig, url=None)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body

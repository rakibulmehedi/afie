"""Pipeline orchestrator use-case: RECEIVED → SYNTHESIZING → DRAFTED → AWAITING_APPROVAL.

Called as a FastAPI background task after IngestUseCase has committed the
ingest_queue row. Errors are logged but NOT re-raised; the caller has already
returned HTTP 200 to QStash.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Final

from app.api.schemas import ConsumePayload
from app.domain.models import SagaStatus
from app.domain.ports import IPayloadGuard, ISagaRepository, ISynthesisPort

logger = logging.getLogger(__name__)

MAX_SYNTHESIS_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 2.0


class SynthesizeUseCase:
    def __init__(
        self,
        saga_repo: ISagaRepository,
        synthesis_port: ISynthesisPort,
        payload_guard: IPayloadGuard,
    ) -> None:
        self.saga_repo = saga_repo
        self.synthesis_port = synthesis_port
        self.payload_guard = payload_guard

    async def execute(self, payload: ConsumePayload) -> None:
        """Full synthesis pipeline driven from ConsumePayload."""
        try:
            from app.infrastructure.db.session import pool  # late import avoids circular at module load

            if pool is None:
                logger.error(
                    "SynthesizeUseCase.execute: pool not initialised — tenant=%s",
                    payload.tenant_id,
                )
                return

            # ------------------------------------------------------------------
            # Phase 1: Resolve ingest_id, fetch blueprint, create saga, advance
            #          RECEIVED → SYNTHESIZING — then COMMIT before calling LLM.
            # ------------------------------------------------------------------
            saga_id: uuid.UUID
            blueprint_id: object
            persona: object
            persona_version: object
            cognitive_state: object
            ingest_id: uuid.UUID

            async with pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(payload.tenant_id),),
                    )

                    # 1. Resolve ingest_queue row by (tenant_id, idempotency_key)
                    cursor = await conn.execute(
                        "SELECT id FROM ingest_queue WHERE tenant_id = %s AND idempotency_key = %s",
                        (str(payload.tenant_id), payload.idempotency_key),
                    )
                    ingest_row = await cursor.fetchone()
                    if ingest_row is None:
                        logger.error(
                            "ingest_queue row not found — tenant=%s idem=%s",
                            payload.tenant_id,
                            payload.idempotency_key,
                        )
                        return
                    ingest_id = ingest_row[0]

                    # 2. Fetch tenant blueprint
                    cursor = await conn.execute(
                        "SELECT id, persona, persona_version, cognitive_state"
                        " FROM tenant_blueprints WHERE tenant_id = %s LIMIT 1",
                        (str(payload.tenant_id),),
                    )
                    blueprint_row_raw = await cursor.fetchone()
                    if blueprint_row_raw is None:
                        logger.error("No blueprint for tenant=%s", payload.tenant_id)
                        return
                    blueprint_id, persona, persona_version, cognitive_state = blueprint_row_raw
                    blueprint_row: dict[str, object] = {
                        "id": blueprint_id,
                        "persona": persona,
                        "persona_version": persona_version,
                        "cognitive_state": cognitive_state,
                    }

                    # 3. Create saga (status = RECEIVED, version = 0)
                    saga_id = await self.saga_repo.create(conn, ingest_id, payload.tenant_id)

                    # 4. RECEIVED → SYNTHESIZING (must commit BEFORE calling LLM)
                    advanced = await self.saga_repo.advance(
                        conn,
                        saga_id,
                        payload.tenant_id,
                        SagaStatus.RECEIVED,
                        SagaStatus.SYNTHESIZING,
                        version=0,
                    )
                    if not advanced:
                        logger.warning(
                            "Stale lock on RECEIVED→SYNTHESIZING saga=%s", saga_id
                        )
                        return
                # Transaction commits here — SYNTHESIZING is now durable.

            # ------------------------------------------------------------------
            # Phase 2: Build prompt + call LLM (outside any DB transaction).
            # ------------------------------------------------------------------
            from app.infrastructure.llm.frameworks import get_synthesis_model  # noqa: PLC0415
            from app.infrastructure.llm.persona import build_system_prompt  # noqa: PLC0415
            from app.synthesis.router import SynthesisError, SynthesisTimeoutError  # noqa: PLC0415

            system_prompt = build_system_prompt(blueprint_row)
            model = get_synthesis_model()
            sanitized = self.payload_guard.sanitize(payload.source, payload.raw_payload)

            llm_output: str | None = None
            last_exc: Exception | None = None
            for attempt in range(MAX_SYNTHESIS_RETRIES):
                try:
                    llm_output = await self.synthesis_port.synthesize(system_prompt, sanitized, model)
                    break
                except (SynthesisTimeoutError, SynthesisError) as exc:
                    last_exc = exc
                    logger.warning(
                        "Synthesis attempt %d/%d failed: %s saga=%s",
                        attempt + 1,
                        MAX_SYNTHESIS_RETRIES,
                        exc,
                        saga_id,
                    )
                    if attempt < MAX_SYNTHESIS_RETRIES - 1:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS)

            # ------------------------------------------------------------------
            # Phase 3a: All retries exhausted → mark FAILED and return.
            # ------------------------------------------------------------------
            if llm_output is None:
                safe_error = (
                    f"{type(last_exc).__name__}: {str(last_exc)[:200]}"
                    if last_exc
                    else "unknown"
                )
                logger.error(
                    "All synthesis retries exhausted saga=%s last_error=%s",
                    saga_id,
                    safe_error,
                )
                async with pool.connection() as conn2:
                    async with conn2.transaction():
                        await conn2.execute(
                            "SET LOCAL app.current_tenant = %s",
                            (str(payload.tenant_id),),
                        )
                        cur = await conn2.execute(
                            "SELECT version FROM feature_sagas WHERE id = %s AND tenant_id = %s",
                            (saga_id, str(payload.tenant_id)),
                        )
                        v_row = await cur.fetchone()
                        if v_row:
                            await self.saga_repo.advance(
                                conn2,
                                saga_id,
                                payload.tenant_id,
                                SagaStatus.SYNTHESIZING,
                                SagaStatus.FAILED,
                                v_row[0],
                            )
                return

            # ------------------------------------------------------------------
            # Phase 3b: LLM succeeded → persist draft, advance to AWAITING_APPROVAL,
            #           mark ingest PROCESSED — all in one transaction.
            # ------------------------------------------------------------------
            async with pool.connection() as conn3:
                async with conn3.transaction():
                    await conn3.execute(
                        "SET LOCAL app.current_tenant = %s",
                        (str(payload.tenant_id),),
                    )

                    # Get current version (after SYNTHESIZING advance bumped it to 1)
                    cur = await conn3.execute(
                        "SELECT version FROM feature_sagas WHERE id = %s AND tenant_id = %s",
                        (saga_id, str(payload.tenant_id)),
                    )
                    v_row = await cur.fetchone()
                    if v_row is None:
                        logger.error("Saga disappeared after synthesis saga=%s", saga_id)
                        return
                    current_version: int = v_row[0]

                    # SYNTHESIZING → DRAFTED
                    if not await self.saga_repo.advance(
                        conn3,
                        saga_id,
                        payload.tenant_id,
                        SagaStatus.SYNTHESIZING,
                        SagaStatus.DRAFTED,
                        current_version,
                    ):
                        logger.warning(
                            "Stale lock SYNTHESIZING→DRAFTED saga=%s", saga_id
                        )
                        return

                    # Insert cspe_drafts row
                    await conn3.execute(
                        """
                        INSERT INTO cspe_drafts (tenant_id, saga_id, blueprint_id, llm_output, approval_status)
                        VALUES (%s, %s, %s, %s, 'PENDING')
                        """,
                        (str(payload.tenant_id), saga_id, blueprint_id, llm_output),
                    )

                    # DRAFTED → AWAITING_APPROVAL (S2.7: set 7-day approval deadline)
                    approval_deadline = datetime.now(timezone.utc) + timedelta(days=7)
                    if not await self.saga_repo.advance(
                        conn3,
                        saga_id,
                        payload.tenant_id,
                        SagaStatus.DRAFTED,
                        SagaStatus.AWAITING_APPROVAL,
                        current_version + 1,
                        deadline_at=approval_deadline,
                    ):
                        logger.warning(
                            "Stale lock DRAFTED→AWAITING_APPROVAL saga=%s", saga_id
                        )
                        return

                    # Mark ingest row as PROCESSED (only once everything commits)
                    await self.saga_repo.mark_ingest_processed(conn3, ingest_id, payload.tenant_id)

            logger.info(
                "Pipeline complete saga=%s tenant=%s", saga_id, payload.tenant_id
            )

        except Exception:
            logger.exception(
                "SynthesizeUseCase.execute failed tenant=%s idem=%s",
                payload.tenant_id,
                payload.idempotency_key,
            )

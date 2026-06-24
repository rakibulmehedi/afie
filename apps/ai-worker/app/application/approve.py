"""ApproveUseCase: business logic for APPROVE/REJECT decisions.

The API adapter (api/approve.py) manages DB connections, QStash publishing,
and HTTP responses. This use case contains only the DB mutation logic.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from app.domain.ports import ISagaRepository

logger = logging.getLogger(__name__)


class ApproveUseCase:
    def __init__(self, saga_repo: ISagaRepository) -> None:
        self.saga_repo = saga_repo

    async def execute(self, conn: object, payload: object) -> tuple[object, object, object]:
        """Process APPROVE or REJECT decision.

        Performs all DB mutations within the caller-managed transaction.
        Returns (saga_id, target_platform, posting_token) for APPROVE,
        (saga_id, None, None) for REJECT.

        Raises HTTPException(404) if draft/saga not found.
        Raises HTTPException(409) on optimistic lock conflict.
        """
        from app.api.schemas import ApprovePayload  # noqa: PLC0415

        p: ApprovePayload = payload  # type: ignore[assignment]

        # Lock and fetch the draft row
        draft_cursor = await conn.execute(  # type: ignore[union-attr]
            """
            SELECT id, saga_id, approval_status
              FROM cspe_drafts
             WHERE id = %s
               FOR UPDATE
            """,
            (str(p.draft_id),),
        )
        draft_row = await draft_cursor.fetchone()
        if draft_row is None:
            raise HTTPException(status_code=404, detail="Draft not found")

        _, saga_id, _ = draft_row

        # Lock and fetch the saga row
        saga_cursor = await conn.execute(  # type: ignore[union-attr]
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

        target_platform: object = None
        posting_token: object = None
        saga_id_out: object = saga_id

        if p.decision == "REJECT":
            await conn.execute(  # type: ignore[union-attr]
                """
                UPDATE cspe_drafts
                   SET approval_status = 'REJECTED',
                       decided_at = now()
                 WHERE id = %s
                """,
                (str(p.draft_id),),
            )

            update_cursor = await conn.execute(  # type: ignore[union-attr]
                """
                UPDATE feature_sagas
                   SET status     = 'REJECTED',
                       updated_at = now()
                 WHERE id        = %s
                   AND tenant_id = %s
                   AND version   = %s
                   AND status    = 'AWAITING_APPROVAL'
                """,
                (str(saga_id), str(p.tenant_id), saga_version),
            )

            if update_cursor.rowcount == 0:
                logger.warning(
                    "Optimistic lock conflict on saga %s (version=%s, status=%s) during REJECT",
                    saga_id,
                    saga_version,
                    saga_status,
                )
                raise HTTPException(
                    status_code=409,
                    detail="Saga version conflict — transition already applied or status changed",
                )

            await conn.execute(  # type: ignore[union-attr]
                """
                INSERT INTO feature_saga_events
                            (saga_id, tenant_id, from_status, to_status, actor)
                VALUES (%s, %s, 'AWAITING_APPROVAL', 'REJECTED', %s)
                """,
                (str(saga_id), str(p.tenant_id), p.actor),
            )

        else:
            # decision == "APPROVE"
            approval_status = "EDITED" if p.edited_content is not None else "APPROVED"

            approve_cursor = await conn.execute(  # type: ignore[union-attr]
                """
                UPDATE cspe_drafts
                   SET approval_status = %s,
                       decided_at      = now(),
                       edited_content  = %s
                 WHERE id = %s
                RETURNING saga_id, target_platform, posting_token, tenant_id
                """,
                (approval_status, p.edited_content, str(p.draft_id)),
            )

            returning_row = await approve_cursor.fetchone()
            if returning_row is None:
                raise HTTPException(status_code=404, detail="Draft not found on APPROVE update")

            _, target_platform, posting_token, _ = returning_row

            update_cursor = await conn.execute(  # type: ignore[union-attr]
                """
                UPDATE feature_sagas
                   SET status     = 'APPROVED',
                       updated_at = now()
                 WHERE id        = %s
                   AND tenant_id = %s
                   AND version   = %s
                   AND status    = 'AWAITING_APPROVAL'
                """,
                (str(saga_id), str(p.tenant_id), saga_version),
            )

            if update_cursor.rowcount == 0:
                logger.warning(
                    "Optimistic lock conflict on saga %s (version=%s, status=%s) during APPROVE",
                    saga_id,
                    saga_version,
                    saga_status,
                )
                raise HTTPException(
                    status_code=409,
                    detail="Saga version conflict — transition already applied or status changed",
                )

            await conn.execute(  # type: ignore[union-attr]
                """
                INSERT INTO feature_saga_events
                            (saga_id, tenant_id, from_status, to_status, actor)
                VALUES (%s, %s, 'AWAITING_APPROVAL', 'APPROVED', %s)
                """,
                (str(saga_id), str(p.tenant_id), p.actor),
            )

        return saga_id_out, target_platform, posting_token

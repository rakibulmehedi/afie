from __future__ import annotations

from app.application.approve import ApproveUseCase
from app.application.ingest import IngestUseCase
from app.application.synthesize import SynthesizeUseCase
from app.infrastructure.db.ingest_repo import IngestRepository
from app.infrastructure.db.saga_repo import SagaRepository
from app.infrastructure.llm.router import SynthesisAdapter
from app.security.payload_guard import PayloadGuard


def build_ingest_use_case() -> IngestUseCase:
    return IngestUseCase(ingest_repo=IngestRepository(), saga_repo=SagaRepository())


def build_synthesize_use_case() -> SynthesizeUseCase:
    return SynthesizeUseCase(
        saga_repo=SagaRepository(),
        synthesis_port=SynthesisAdapter(),
        payload_guard=PayloadGuard(),
    )


def build_approve_use_case() -> ApproveUseCase:
    return ApproveUseCase(saga_repo=SagaRepository())

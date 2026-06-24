from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.core.settings import get_settings
from app.db.session import create_pool, pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    await create_pool(settings.database_url)
    yield
    if pool is not None:
        await pool.close()


app = FastAPI(lifespan=lifespan)

from app.api.approve import router as approve_router  # noqa: E402
from app.api.consume import router as consume_router  # noqa: E402

app.include_router(consume_router, prefix="/api/v1")
app.include_router(approve_router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

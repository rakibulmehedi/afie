from collections.abc import AsyncGenerator

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

pool: AsyncConnectionPool | None = None


async def create_pool(dsn: str) -> None:
    global pool
    pool = AsyncConnectionPool(conninfo=dsn, open=False)
    await pool.open()


async def get_conn() -> AsyncGenerator[AsyncConnection, None]:
    if pool is None:
        raise RuntimeError("Connection pool is not initialised — call create_pool() at startup")
    async with pool.connection() as conn:
        yield conn

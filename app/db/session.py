from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_size=settings.POOL_SIZE, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_schema() -> None:
    import asyncpg
    from pathlib import Path
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    sql = (Path(__file__).parent / "schema.sql").read_text()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()

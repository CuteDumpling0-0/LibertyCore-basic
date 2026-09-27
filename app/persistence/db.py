"""
app/persistence/db.py — SQLAlchemy async engine, session factory, and Base.
Supports SQLite (local dev) and PostgreSQL (production) via DATABASE_URL.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_async_engine(
        settings.database_url,
        echo=False,
        connect_args=connect_args,
    )


engine = _make_engine()

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    async with AsyncSessionLocal() as session:
        yield session


async def create_all_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight compatibility migration for existing local SQLite databases.
        if engine.url.drivername.startswith("sqlite"):
            columns = (await conn.exec_driver_sql("PRAGMA table_info(tasks)")).fetchall()
            if "runtime_state_json" not in {column[1] for column in columns}:
                await conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN runtime_state_json TEXT")

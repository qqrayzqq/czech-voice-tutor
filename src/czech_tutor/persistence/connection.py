from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


async def check_database_connection(database_url: str) -> None:
    """Run a read-only connectivity check and release the temporary engine."""
    engine = create_database_engine(database_url)

    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))

            if result.scalar_one() != 1:
                raise RuntimeError("Unexpected database connection check result.")
    finally:
        await engine.dispose()


def create_database_engine(database_url: str) -> AsyncEngine:
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    return create_async_engine(
        url,
        connect_args={"connect_timeout": 10},
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

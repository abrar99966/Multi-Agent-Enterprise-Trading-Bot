from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./trading_bot.db")
# echo=True logs every SQL statement to stdout — that murders perf when the
# dashboard polls 4 endpoints every 15-30s. Opt-in via SQL_ECHO=1.
SQL_ECHO = os.getenv("SQL_ECHO", "0").lower() in ("1", "true", "yes")

engine = create_async_engine(DATABASE_URL, echo=SQL_ECHO)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db():
    async with async_session() as session:
        yield session

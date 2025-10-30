import asyncio
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.pool import NullPool
from typing import Annotated, AsyncGenerator
from fastapi import Depends
import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DATABASE = os.getenv("DB_DATABASE")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_DATABASE}"

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool if not PRODUCTION else None
)

def get_safe_db_url(url: str) -> str:
    """Return database URL with password masked"""
    try:
        if '@' in url:
            prefix = url.split('://')[0]
            rest = url.split('://')[1]
            credentials, host_db = rest.split('@')
            username = credentials.split(':')[0]
            return f"{prefix}://{username}:****@{host_db}"
        return url
    except:
        return "Invalid URL format"

async def check_db_connection(max_retries: int = 3, retry_delay: int = 2) -> bool:
    """Check if database connection is working with retries"""
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1 as health_check"))
                row = result.fetchone()
                if row and row[0] == 1:
                    return True
        except Exception as e:
            print(f"❌ Connection attempt {attempt}/{max_retries} failed: {type(e).__name__}: {e}")
            if attempt < max_retries:
                print(f"⏳ Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
    return False

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine) as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]

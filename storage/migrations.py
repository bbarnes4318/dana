"""Database Migration Runner.

Supports running programmatically (lazy initialization) or as a CLI tool:
python -m storage.migrations
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def run_migrations(pool_or_conn: Any) -> None:
    """Run database migrations in a transaction, tracking applied versions."""
    # Find 001_initial.sql relative to migrations folder
    migration_file = Path("migrations/001_initial.sql")
    if not migration_file.exists():
        migration_file = Path(__file__).parent.parent / "migrations" / "001_initial.sql"
        
    if not migration_file.exists():
        raise FileNotFoundError(f"Migration file 001_initial.sql not found at {migration_file.absolute()}")

    logger.info("Initializing schema_migrations table...")
    # First, make sure schema_migrations exists
    create_tracking_table_sql = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    
    with open(migration_file, "r", encoding="utf-8") as f:
        migration_sql = f.read()

    # Determine connection type
    is_pool = hasattr(pool_or_conn, "acquire")
    
    if is_pool:
        async with pool_or_conn.acquire() as conn:
            await conn.execute(create_tracking_table_sql)
            row = await conn.fetchrow(
                "SELECT version FROM schema_migrations WHERE version = $1;", 
                "001_initial"
            )
            if not row:
                logger.info("Applying 001_initial migration...")
                async with conn.transaction():
                    await conn.execute(migration_sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1);", 
                        "001_initial"
                    )
                logger.info("001_initial migration applied successfully.")
            else:
                logger.info("001_initial migration is already applied.")
    else:
        await pool_or_conn.execute(create_tracking_table_sql)
        row = await pool_or_conn.fetchrow(
            "SELECT version FROM schema_migrations WHERE version = $1;", 
            "001_initial"
        )
        if not row:
            logger.info("Applying 001_initial migration...")
            async with pool_or_conn.transaction():
                await pool_or_conn.execute(migration_sql)
                await pool_or_conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1);", 
                    "001_initial"
                )
            logger.info("001_initial migration applied successfully.")
        else:
            logger.info("001_initial migration is already applied.")


async def main() -> int:
    """CLI runner main entrypoint."""
    logging.basicConfig(level=logging.INFO)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set. Skipping migrations.", file=sys.stderr)
        return 0
        
    try:
        print("Connecting to database to run migrations...")
        conn = await asyncpg.connect(dsn)
        try:
            await run_migrations(conn)
            print("Database migrations applied successfully.")
        finally:
            await conn.close()
        return 0
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

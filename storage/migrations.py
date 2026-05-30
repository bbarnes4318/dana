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
    # Find migrations folder relative to workdir or module
    migrations_dir = Path("migrations")
    if not migrations_dir.exists():
        migrations_dir = Path(__file__).parent.parent / "migrations"
        
    if not migrations_dir.exists():
        raise FileNotFoundError(f"Migrations directory not found at {migrations_dir.absolute()}")

    logger.info("Initializing schema_migrations table...")
    # First, make sure schema_migrations exists
    create_tracking_table_sql = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    
    # Get all .sql files, sorted alphabetically
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        logger.warning("No SQL migration files found in %s", migrations_dir.absolute())
        return

    # Determine connection type
    is_pool = hasattr(pool_or_conn, "acquire")
    
    async def apply_migration(conn: Any, file_path: Path) -> None:
        version = file_path.stem
        row = await conn.fetchrow(
            "SELECT version FROM schema_migrations WHERE version = $1;", 
            version
        )
        if not row:
            logger.info("Applying migration %s...", version)
            with open(file_path, "r", encoding="utf-8") as f:
                migration_sql = f.read()
            async with conn.transaction():
                await conn.execute(migration_sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1);", 
                    version
                )
            logger.info("Migration %s applied successfully.", version)
        else:
            logger.info("Migration %s is already applied.", version)

    if is_pool:
        async with pool_or_conn.acquire() as conn:
            await conn.execute(create_tracking_table_sql)
            for sql_file in sql_files:
                await apply_migration(conn, sql_file)
    else:
        await pool_or_conn.execute(create_tracking_table_sql)
        for sql_file in sql_files:
            await apply_migration(pool_or_conn, sql_file)



async def main() -> int:
    """CLI runner main entrypoint."""
    logging.basicConfig(level=logging.INFO)
    dsn = os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("Neither DATABASE_ADMIN_URL nor DATABASE_URL is set. Skipping migrations.", file=sys.stderr)
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

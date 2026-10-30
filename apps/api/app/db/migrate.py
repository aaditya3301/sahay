"""Apply schema.sql as the owner role and set up the runtime role (no BYPASSRLS)."""

import asyncio
from pathlib import Path

from app.config import settings
from app.db import engine

SCHEMA = Path(__file__).with_name("schema.sql")


async def main() -> None:
    eng = engine(owner=True)
    async with eng.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        exists = await raw.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", settings.db_user)
        pw = settings.db_password.replace("'", "''")
        if not exists:
            await raw.execute(f"CREATE ROLE {settings.db_user} LOGIN PASSWORD '{pw}' NOBYPASSRLS")
        else:
            await raw.execute(f"ALTER ROLE {settings.db_user} WITH LOGIN PASSWORD '{pw}' NOBYPASSRLS")
        await raw.execute(SCHEMA.read_text(encoding="utf-8"))
        u = settings.db_user
        await raw.execute(
            f"""
            GRANT USAGE ON SCHEMA public TO {u};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {u};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {u};
            REVOKE UPDATE, DELETE ON audit_log FROM {u};
            """
        )
        rls = await raw.fetch(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='tenant_id' "
            "WHERE c.relkind='r' AND c.relnamespace='public'::regnamespace"
        )
        unforced = [r["relname"] for r in rls if not (r["relrowsecurity"] and r["relforcerowsecurity"])]
        global_ok = {"tenant_members", "channel_routes"}
        bad = [t for t in unforced if t not in global_ok]
        print(f"schema applied; tenant tables with forced RLS: {len(rls) - len(unforced)}")
        if bad:
            raise SystemExit(f"RLS missing on: {bad}")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())

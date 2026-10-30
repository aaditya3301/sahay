"""Run the scheduler tick for one tenant as if it were a given IST time (checks what the single job triggers).

    cd apps/api && PYTHONPATH=. uv run python ../../scripts/tick_check.py loop-loot 2026-11-01T21:00
"""
import asyncio
import sys
from datetime import datetime

from app.db import system_session
from app.lifecycle import IST, tick


async def main():
    slug, when = sys.argv[1], datetime.fromisoformat(sys.argv[2]).replace(tzinfo=IST)
    async with system_session() as db:
        tid = str((await db.one("SELECT id FROM tenants WHERE slug=:s", s=slug))["id"])
    print(await tick(tid, when))


asyncio.run(main())

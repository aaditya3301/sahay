import asyncio, numpy as np
from app.db import system_session, tenant_session
from app.insights import _cluster
async def main():
    async with system_session() as db: t = await db.val("SELECT id FROM tenants WHERE slug='loop-loot'")
    async with tenant_session(t) as db:
        rows = await db.all("SELECT complaint_summary, complaint_embedding FROM tickets WHERE complaint_embedding IS NOT NULL AND NOT is_simulation")
    v = np.array([r["complaint_embedding"] for r in rows])
    for th in (0.12, 0.15, 0.2, 0.25):
        labels = _cluster(v, th); sizes = sorted(np.bincount(labels), reverse=True)
        print(th, "clusters:", len(sizes), "sizes:", sizes[:8])
asyncio.run(main())

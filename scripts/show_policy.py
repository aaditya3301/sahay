import asyncio, json
from app.db import system_session, tenant_session
from app import policy as pol
async def main():
    async with system_session() as db:
        ts = await db.all("SELECT id, slug FROM tenants")
    for t in ts:
        async with tenant_session(t["id"]) as db:
            r = await db.one("SELECT rules, questions FROM policies WHERE status='active'")
        doc = pol.PolicyDoc.model_validate(r["rules"])
        print("==", t["slug"], "limits", doc.limits.model_dump(), "unmatched", doc.unmatched_decision)
        for rule in doc.rules:
            c = pol.render_rule(rule); print(" ", c["headline"], "|", " AND ".join(c["when"]))
        print("  questions:", r["questions"])
asyncio.run(main())

"""Webhooks we send to the business (their Slack, CRM, ...): ticket.created, ticket.escalated, ticket.resolved,
lead.created, call.completed, plus webhook.test.

Each delivery is signed like our requests to their endpoints:
    X-Sahay-Timestamp: <unix seconds>
    X-Sahay-Signature: sha256=HMAC_SHA256(endpoint_secret, "<timestamp>.<raw body>")
and retried three times (after ~10s and ~60s). Every attempt is logged in webhook_deliveries.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time

import httpx

from app.config import settings
from app.core import enqueue, jsonable, new_id
from app.db import tenant_session

log = logging.getLogger("sahay.webhooks")
EVENTS = ["ticket.created", "ticket.escalated", "ticket.resolved", "lead.created", "call.completed"]
RETRY_DELAYS = [0, 10, 60]


def _allowed(url: str) -> bool:
    from app.profile import _safe_url

    if settings.env == "dev" and url.startswith(("http://localhost", "http://127.0.0.1")):
        return True
    return _safe_url(url)


async def emit(db, event: str, data: dict) -> int:
    """Queue `event` for every active endpoint subscribed to it. Call inside the caller's transaction."""
    eps = await db.all("SELECT id, events FROM webhook_endpoints WHERE active")
    ids = []
    for ep in eps:
        if "*" in (ep["events"] or []) or event in (ep["events"] or []):
            row = await db.one("INSERT INTO webhook_deliveries (tenant_id, endpoint_id, event, payload) VALUES (:t,:e,:ev,:p) RETURNING id",
                               t=db.tenant_id, e=ep["id"], ev=event, p=jsonable({"id": new_id(), "type": event, "created_at": int(time.time()), "data": data}))
            ids.append(str(row["id"]))
    for i in ids:
        enqueue(deliver, db.tenant_id, i, delay=1.5, name="webhook", queue="lifecycle")
    return len(ids)


async def deliver(tenant_id: str, delivery_id: str) -> None:
    d = None
    for _ in range(5):  # the emitting transaction may still be committing
        async with tenant_session(tenant_id) as db:
            d = await db.one("SELECT d.*, e.url, e.secret FROM webhook_deliveries d JOIN webhook_endpoints e ON e.id=d.endpoint_id WHERE d.id=:id", id=delivery_id)
        if d:
            break
        await asyncio.sleep(1)
    if not d or d["status"] == "delivered":
        return
    body = json.dumps(d["payload"], default=str)
    for wait in RETRY_DELAYS:
        if wait:
            await asyncio.sleep(wait)
        code, err = None, None
        if not _allowed(d["url"]):
            code, err = None, "URL must be a public https:// address"
        else:
            ts = str(int(time.time()))
            sig = "sha256=" + hmac.new(d["secret"].encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
            try:
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(d["url"], content=body, headers={"Content-Type": "application/json", "X-Sahay-Event": d["event"],
                                                                      "X-Sahay-Delivery": str(d["id"]), "X-Sahay-Timestamp": ts, "X-Sahay-Signature": sig,
                                                                      "User-Agent": "Sahay-Webhooks/1"})
                code = r.status_code
                if code >= 300:
                    err = r.text[:200]
            except Exception as e:
                err = str(e)[:200]
        ok = code is not None and code < 300
        async with tenant_session(tenant_id) as db:
            await db.exec("UPDATE webhook_deliveries SET attempts=attempts+1, last_status_code=:c, last_error=:e, status=:s, "
                          "delivered_at=CASE WHEN :s='delivered' THEN now() END WHERE id=:id",
                          c=code, e=err, s="delivered" if ok else ("failed" if wait == RETRY_DELAYS[-1] or err and "public https" in err else "retrying"),
                          id=delivery_id)
        if ok or (err and "public https" in err):
            return


def ticket_payload(ticket: dict, customer: dict | None, prefix: str | None) -> dict:
    from app.core import ticket_ref

    return {"ref": ticket_ref(prefix, ticket.get("number")), "number": ticket.get("number"), "status": ticket.get("status"),
            "intent": ticket.get("intent"), "urgency": ticket.get("urgency"), "summary": ticket.get("summary"),
            "order_refs": ticket.get("order_refs") or [], "sensitive": ticket.get("sensitive"),
            "customer": {"name": (customer or {}).get("display_name"), "email": (customer or {}).get("primary_email"),
                         "phone": (customer or {}).get("primary_phone")},
            "url": f"{settings.public_base_url.rstrip('/')}/tickets/{ticket.get('id')}"}


async def emit_ticket(db, event: str, ticket_id) -> None:
    t = await db.one("SELECT * FROM tickets WHERE id=:id", id=ticket_id)
    if not t or t["is_simulation"]:
        return
    c = await db.one("SELECT * FROM customers WHERE id=:c", c=t["customer_id"])
    prefix = await db.val("SELECT ticket_prefix FROM tenant_settings")
    await emit(db, event, ticket_payload(t, c, prefix))

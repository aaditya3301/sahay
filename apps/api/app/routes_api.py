"""Public REST API for the business's own website/server, authenticated with its secret key:

    Authorization: Bearer sk_...

  POST /v1/api/events     their site tells us what happened (order placed/shipped/delivered/cancelled, product
                          updated, customer updated, cart abandoned, return/exchange shipped). Idempotent by event id.
  POST /v1/api/tickets    create a ticket from their own contact form (goes through the same AI pipeline)
  GET  /v1/api/tickets    a customer's tickets (by email / phone / ref)
  POST /v1/api/leads      create a sales lead from their form
  GET  /v1/api/ping       check the key (and see which business it belongs to)

Tickets and conversations live with us; orders, products and customers live with them and reach us through
events or the Commerce Contract endpoint (see commerce.CustomCommerce).
"""

import hashlib
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.core import audit, enqueue, jsonable, norm_email, norm_phone, ticket_ref
from app.db import system_session, tenant_session

router = APIRouter(prefix="/v1/api")


async def api_tenant(authorization: str | None) -> str:
    key = (authorization or "").removeprefix("Bearer ").strip()
    if not key.startswith("sk_"):
        raise HTTPException(401, "Send your secret key as: Authorization: Bearer sk_...")
    async with system_session() as db:
        r = await db.one("SELECT tenant_id FROM channel_routes WHERE channel='secret' AND external_key=:k", k=hashlib.sha256(key.encode()).hexdigest())
    if not r:
        raise HTTPException(401, "Unknown or rotated secret key")
    return str(r["tenant_id"])


@router.get("/ping")
async def ping(authorization: str | None = Header(None)):
    tid = await api_tenant(authorization)
    async with tenant_session(tid) as db:
        t = await db.one("SELECT id, slug, name FROM tenants WHERE id=:t", t=tid)
    return jsonable({"ok": True, "business": t})


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class Event(BaseModel):
    id: str
    type: str
    data: dict = {}
    occurred_at: str | None = None


class EventBatch(BaseModel):
    events: list[Event]


ORDER_EVENTS = {"order.created", "order.placed", "order.updated", "order.paid", "order.shipped", "order.delivered", "order.cancelled"}
RETURN_EVENTS = {"return.created", "return.updated", "return.shipped", "exchange.created", "exchange.shipped", "return.completed"}


def _ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except Exception:
        return None


async def _upsert_order(db, d: dict, event: str) -> dict:
    ref = str(d.get("orderRef") or d.get("order_ref") or d.get("id") or "").lstrip("#")
    if not ref:
        raise ValueError("order events need data.orderRef")
    status = {"order.shipped": "in_transit", "order.delivered": "delivered", "order.cancelled": "cancelled"}.get(event) or (d.get("status") or "processing").lower()
    items = [{"sku": i.get("sku"), "title": i.get("title"), "qty": int(i.get("qty") or 1), "price_paise": int(round(float(i.get("price") or 0) * 100))}
             for i in d.get("items") or []]
    row = await db.one(
        "INSERT INTO commerce_orders (tenant_id, order_ref, customer_name, customer_phone, customer_email, line_items, total_paise, payment_method, status, "
        "shipping_address, courier, awb, tracking_url, shipment_status, expected_delivery, delivered_at, placed_at) "
        "VALUES (:t,:r,:n,:p,:e,:li,:tot,:pm,:st,:addr,:c,:awb,:url,:ss,:eta,:dl, coalesce(CAST(:pl AS timestamptz), now())) "
        "ON CONFLICT (tenant_id, order_ref) DO UPDATE SET customer_name=coalesce(nullif(:n,''), commerce_orders.customer_name), "
        "customer_phone=coalesce(:p, commerce_orders.customer_phone), customer_email=coalesce(:e, commerce_orders.customer_email), "
        "line_items=CASE WHEN jsonb_array_length(CAST(:li AS jsonb)) > 0 THEN CAST(:li AS jsonb) ELSE commerce_orders.line_items END, "
        "total_paise=CASE WHEN :tot > 0 THEN :tot ELSE commerce_orders.total_paise END, status=:st, "
        "courier=coalesce(:c, commerce_orders.courier), awb=coalesce(:awb, commerce_orders.awb), tracking_url=coalesce(:url, commerce_orders.tracking_url), "
        "shipment_status=coalesce(:ss, commerce_orders.shipment_status), expected_delivery=coalesce(CAST(:eta AS date), commerce_orders.expected_delivery), "
        "delivered_at=coalesce(CAST(:dl AS timestamptz), commerce_orders.delivered_at) RETURNING *",
        t=db.tenant_id, r=ref, n=d.get("customerName") or "", p=norm_phone(d.get("customerPhone")), e=norm_email(d.get("customerEmail")), li=items,
        tot=int(round(float(d.get("total") or 0) * 100)), pm=(d.get("paymentMethod") or "prepaid").lower(), st=status,
        addr=d.get("shippingAddress") or {}, c=d.get("courier"), awb=d.get("trackingNumber"), url=d.get("trackingUrl"),
        ss={"order.shipped": "in_transit", "order.delivered": "delivered"}.get(event, d.get("shipmentStatus")),
        eta=_ts(d.get("expectedDelivery")).date() if _ts(d.get("expectedDelivery")) else None,
        dl=_ts(d.get("deliveredAt")) or (datetime.now().astimezone() if event == "order.delivered" else None), pl=_ts(d.get("placedAt")),
    )
    return row


async def _notify(db, order: dict, text: str, kind: str) -> dict:
    """Tell the customer on Telegram (or email). If an open ticket is about this order, the message joins that ticket."""
    from app.channels import send_to_customer
    from app.lifecycle import _customer_conv

    if not (order.get("customer_phone") or order.get("customer_email")):
        return {"notified": False, "reason": "no contact on the order"}
    channel = "telegram" if order.get("customer_phone") else "email"
    cust, conv = await _customer_conv(db, order.get("customer_name"), order.get("customer_phone"), order.get("customer_email"), channel)
    ticket = await db.one("SELECT id, number FROM tickets WHERE customer_id=:c AND :r = ANY(order_refs) AND status NOT IN ('closed') "
                          "ORDER BY updated_at DESC LIMIT 1", c=cust["id"], r=order["order_ref"]) or await db.one(
        "SELECT id, number FROM tickets WHERE customer_id=:c AND status NOT IN ('resolved','closed') AND updated_at > now() - interval '14 days' "
        "AND NOT is_simulation ORDER BY updated_at DESC LIMIT 1", c=cust["id"])
    msg = await send_to_customer(db, conv, text, ticket and ticket["id"], "ai", "Order update", subject=f"Update on your order #{order['order_ref']}")
    await db.exec("INSERT INTO lifecycle_events (tenant_id, kind, ref, customer_id, summary, data) VALUES (:t,:k,:r,:c,:s,:d) ON CONFLICT DO NOTHING",
                  t=db.tenant_id, k=kind, r=f"{order['order_ref']}:{kind}", c=cust["id"], s=f"{kind.replace('_', ' ').title()} for #{order['order_ref']}",
                  d={"text": text, "status": msg["status"], "ticket": ticket and ticket["number"]})
    return {"notified": True, "ticket": ticket and ticket["number"], "status": msg["status"]}


async def process_event(db, ev: Event) -> dict:
    d, t = ev.data or {}, ev.type
    first = lambda o: (o.get("customer_name") or "").split(" ")[0]  # noqa: E731
    if t in ORDER_EVENTS:
        o = await _upsert_order(db, d, t)
        out: dict = {"order": o["order_ref"], "status": o["status"]}
        if t == "order.shipped":
            track = f"\nTrack it: {o['tracking_url']}" if o.get("tracking_url") else (f"\nAWB: {o['awb']}" if o.get("awb") else "")
            out |= await _notify(db, o, f"Hi {first(o)} 👋 your order #{o['order_ref']} has shipped{' via ' + o['courier'] if o.get('courier') else ''}.{track}", "shipment_update")
        elif t == "order.delivered":
            open_t = await db.one("SELECT 1 FROM tickets t JOIN customers c ON c.id=t.customer_id WHERE :r = ANY(t.order_refs) AND t.status NOT IN ('resolved','closed')", r=o["order_ref"])
            if open_t:
                out |= await _notify(db, o, f"Hi {first(o)}, your order #{o['order_ref']} shows as delivered ✅ Reply here if anything isn't right.", "delivery_update")
        return out
    if t in RETURN_EVENTS:
        ref = str(d.get("orderRef") or "").lstrip("#")
        o = await db.one("SELECT * FROM commerce_orders WHERE order_ref=:r", r=ref)
        if not o:
            raise ValueError(f"unknown order {ref}")
        if t.endswith(".shipped"):
            track = f"\nTrack it: {d['trackingUrl']}" if d.get("trackingUrl") else (f"\nAWB: {d['trackingNumber']}" if d.get("trackingNumber") else "")
            what = "replacement" if t.startswith("exchange") else "return pickup"
            return {"order": ref} | await _notify(db, o, f"Hi {first(o)} 👋 your {what} for order #{ref} is on its way{' via ' + d['courier'] if d.get('courier') else ''}.{track}", "return_update")
        return {"order": ref, "recorded": True}
    if t in ("product.created", "product.updated", "product.deleted"):
        sku = str(d.get("sku") or "")
        if not sku:
            raise ValueError("product events need data.sku")
        await db.exec(
            "INSERT INTO commerce_products (tenant_id, sku, title, description, category, price_paise, stock) VALUES (:t,:s,:ti,:d,:c,:p,:st) "
            "ON CONFLICT (tenant_id, sku) DO UPDATE SET title=coalesce(nullif(:ti,''), commerce_products.title), description=coalesce(nullif(:d,''), commerce_products.description), "
            "category=coalesce(nullif(:c,''), commerce_products.category), price_paise=CASE WHEN :p > 0 THEN :p ELSE commerce_products.price_paise END, stock=:st",
            t=db.tenant_id, s=sku, ti=d.get("title") or "", d=d.get("description") or "", c=d.get("category") or "",
            p=int(round(float(d.get("price") or 0) * 100)), st=0 if t == "product.deleted" else int(d.get("stock") or 0))
        return {"product": sku}
    if t in ("customer.created", "customer.updated"):
        from app.pipeline import InboundMessage, resolve_customer

        c = await resolve_customer(db, InboundMessage(channel="api", from_phone=d.get("phone"), from_email=d.get("email"), from_name=d.get("name"), verified=True))
        if d.get("id"):
            await db.exec("INSERT INTO customer_identities (tenant_id, customer_id, kind, value, verified) VALUES (:t,:c,'external_id',:v,true) ON CONFLICT DO NOTHING",
                          t=db.tenant_id, c=c["id"], v=str(d["id"]))
        if d.get("vip") is not None:
            await db.exec("UPDATE customers SET vip=:v WHERE id=:c", v=bool(d["vip"]), c=c["id"])
        return {"customer": str(c["id"])}
    if t in ("cart.updated", "checkout.abandoned"):
        items = [{"sku": i.get("sku"), "title": i.get("title") or i.get("sku"), "qty": int(i.get("qty") or 1), "price_paise": int(round(float(i.get("price") or 0) * 100))}
                 for i in d.get("items") or []]
        await db.exec(
            "INSERT INTO commerce_carts (tenant_id, cart_ref, customer_name, customer_phone, customer_email, items, total_paise, checkout_url, source) "
            "VALUES (:t,:r,:n,:p,:e,:i,:tot,:u,'api') ON CONFLICT (tenant_id, cart_ref) DO UPDATE SET items=:i, total_paise=:tot, "
            "checkout_url=coalesce(:u, commerce_carts.checkout_url), status=CASE WHEN commerce_carts.status='converted' THEN 'converted' ELSE 'open' END, updated_at=now()",
            t=db.tenant_id, r=str(d.get("cartId") or d.get("id") or ev.id), n=d.get("customerName"), p=norm_phone(d.get("customerPhone")),
            e=norm_email(d.get("customerEmail")), i=items, tot=sum(i["price_paise"] * i["qty"] for i in items),
            u=d.get("checkoutUrl") if str(d.get("checkoutUrl") or "").startswith("https://") else None)
        return {"cart": True}
    return {"ignored": True, "reason": f"unknown event type {t}"}


async def _ingest_event(tid: str, ev: Event) -> dict:
    async with tenant_session(tid) as db:
        if await db.one("SELECT 1 FROM integration_events WHERE external_id=:e", e=ev.id):
            return {"id": ev.id, "duplicate": True}
    try:
        async with tenant_session(tid) as db:
            res = await process_event(db, ev)
            await db.exec("INSERT INTO integration_events (tenant_id, external_id, type, data, status, result) VALUES (:t,:e,:ty,:d,:s,:r)",
                          t=tid, e=ev.id, ty=ev.type, d=ev.data, s="ignored" if res.get("ignored") else "processed", r=jsonable(res))
        return {"id": ev.id, **jsonable(res)}
    except Exception as e:
        async with tenant_session(tid) as db:
            await db.exec("INSERT INTO integration_events (tenant_id, external_id, type, data, status, result) VALUES (:t,:e,:ty,:d,'failed',:r) "
                          "ON CONFLICT DO NOTHING", t=tid, e=ev.id, ty=ev.type, d=ev.data, r={"error": str(e)[:300]})
        return {"id": ev.id, "error": str(e)[:300]}


@router.post("/events")
async def events(request: Request, authorization: str | None = Header(None)):
    tid = await api_tenant(authorization)
    body = await request.json()
    items = [Event.model_validate(e) for e in body["events"]] if isinstance(body, dict) and "events" in body else [Event.model_validate(body)]
    if len(items) > 100:
        raise HTTPException(400, "Send at most 100 events per request")
    results = [await _ingest_event(tid, ev) for ev in items]
    return {"results": results} if len(results) > 1 else results[0]


# ---------------------------------------------------------------------------
# Tickets + leads from their own forms
# ---------------------------------------------------------------------------
class ApiCustomer(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    external_id: str | None = None


class ApiTicket(BaseModel):
    customer: ApiCustomer
    subject: str | None = None
    message: str
    order_ref: str | None = None


@router.post("/tickets")
async def create_ticket(body: ApiTicket, authorization: str | None = Header(None)):
    from app.pipeline import InboundMessage, ingest

    tid = await api_tenant(authorization)
    c = body.customer
    if not (c.email or c.phone):
        raise HTTPException(400, "customer.email or customer.phone is required so we can reply")
    text = "\n".join(x for x in [f"Subject: {body.subject}" if body.subject else "", body.message, f"Order: #{body.order_ref}" if body.order_ref else ""] if x)
    m = await ingest(tid, InboundMessage(channel="email" if c.email else "telegram", text=text, from_email=c.email, from_phone=c.phone,
                                         from_name=c.name, subject=body.subject, verified=True))
    if c.external_id and m:
        async with tenant_session(tid) as db:
            await db.exec("INSERT INTO customer_identities (tenant_id, customer_id, kind, value, verified) VALUES (:t,:c,'external_id',:v,true) ON CONFLICT DO NOTHING",
                          t=tid, c=m["customer_id"], v=c.external_id)
    return jsonable({"accepted": True, "message_id": m and m["id"], "note": "The AI triages it in a few seconds; poll GET /v1/api/tickets?email=... or listen for ticket.created."})


@router.get("/tickets")
async def list_tickets(authorization: str | None = Header(None), email: str = "", phone: str = "", ref: str = ""):
    tid = await api_tenant(authorization)
    async with tenant_session(tid) as db:
        prefix = await db.val("SELECT ticket_prefix FROM tenant_settings")
        if ref:
            num = ref.split("-")[-1]
            rows = await db.all("SELECT * FROM tickets WHERE number=:n", n=int(num)) if num.isdigit() else []
        else:
            kind, val = ("email", norm_email(email)) if email else ("phone", norm_phone(phone))
            if not val:
                raise HTTPException(400, "Pass email, phone or ref")
            rows = await db.all("SELECT t.* FROM tickets t JOIN customer_identities i ON i.customer_id=t.customer_id WHERE i.kind=:k AND i.value=:v "
                                "ORDER BY t.created_at DESC LIMIT 20", k=kind, v=val)
    return jsonable([{"ref": ticket_ref(prefix, r["number"]), "status": r["status"], "intent": r["intent"], "summary": r["summary"],
                      "order_refs": r["order_refs"], "created_at": r["created_at"], "resolved_at": r["resolved_at"]} for r in rows])


class ApiLead(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    interest: str | None = None
    notes: str | None = None


@router.post("/leads")
async def create_lead(body: ApiLead, authorization: str | None = Header(None)):
    from app import sales

    tid = await api_tenant(authorization)
    if not (body.email or body.phone):
        raise HTTPException(400, "email or phone is required")
    async with tenant_session(tid) as db:
        row, created = await sales.upsert_lead(db, source="api", **body.model_dump())
        await audit(db, "api", None, "lead.created" if created else "lead.updated", "lead", row["id"])
    enqueue(sales.qualify_lead, tid, str(row["id"]), name="qualify", queue="sales", delay=1)
    return jsonable({"lead_id": row["id"], "created": created})

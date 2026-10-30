"""Cashfree payments for the demo storefronts (/store/<site key>).

  checkout → we price the cart from our catalogue (never the browser), save the order as payment_pending in the
             store's commerce_orders with a random order id (LL-7KQ3XD), create the Cashfree order and hand the
             browser its payment_session_id; the Cashfree JS SDK sends the shopper to the hosted payment page
  return   → Cashfree redirects to /store/<key>?order_id=…; the page calls confirm, we ask Cashfree for the status
             and mark the order paid. In sandbox, coming back without paying also counts (payments_confirm_on_return)
  webhook  → /webhooks/cashfree (signed) marks the order paid even if the shopper never comes back

The order id is random, so the agent treats it as proof of purchase: a shopper who quotes it in chat or on a call
gets that order's details (see tools.get_order).
"""

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

import httpx

from app.config import settings
from app.core import audit, norm_email, norm_phone, now
from app.db import tenant_session

log = logging.getLogger("sahay.payments")
REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L, so it reads out cleanly on a call


def configured() -> bool:
    return bool(settings.cashfree_app_id and settings.cashfree_secret_key)


def _headers() -> dict:
    return {"x-client-id": settings.cashfree_app_id, "x-client-secret": settings.cashfree_secret_key,
            "x-api-version": settings.cashfree_api_version, "content-type": "application/json"}


def _api(path: str) -> str:
    return settings.cashfree_api_url.rstrip("/") + path


async def cashfree_order(order_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(_api(f"/orders/{order_id}"), headers=_headers())
    if r.status_code >= 300:
        raise RuntimeError(f"Cashfree: {r.json().get('message', r.text[:160])}")
    return r.json()


class CheckoutError(ValueError):
    pass


async def create_checkout(tenant_id: str, site_key: str, items: list[dict], name: str, phone: str, email: str | None) -> dict:
    if not configured():
        raise CheckoutError("Payments aren't set up for this store yet.")
    phone_n = norm_phone(phone)
    if not phone_n or len(phone_n) != 13 or not phone_n.startswith("+91"):
        raise CheckoutError("Enter a 10-digit Indian mobile number.")
    name = " ".join((name or "").split())[:100]
    if len(name) < 3:
        raise CheckoutError("Enter your name (at least 3 letters).")
    email_n = norm_email(email) if email else None
    async with tenant_session(tenant_id) as db:
        lines, total = [], 0
        for it in items[:30]:
            p = await db.one("SELECT sku, title, price_paise, stock FROM commerce_products WHERE upper(sku)=upper(:s)", s=str(it.get("sku") or ""))
            qty = int(it.get("qty") or 0)
            if not p or qty < 1:
                continue
            if p["stock"] < qty:
                raise CheckoutError(f"Only {p['stock']} of {p['title']} left in stock.")
            qty = min(qty, 20)
            lines.append({"sku": p["sku"], "title": p["title"], "qty": qty, "price_paise": p["price_paise"]})
            total += p["price_paise"] * qty
        if not lines or total < 100:
            raise CheckoutError("Your cart is empty.")
        srow = await db.one("SELECT s.ticket_prefix, t.name, t.slug FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        prefix = (srow["ticket_prefix"] or "OD")[:3].upper()
        for _ in range(5):
            ref = f"{prefix}-{''.join(secrets.choice(REF_ALPHABET) for _ in range(6))}"
            if not await db.val("SELECT 1 FROM commerce_orders WHERE order_ref=:r", r=ref):
                break
        await db.exec(
            "INSERT INTO commerce_orders (tenant_id, order_ref, customer_name, customer_phone, customer_email, line_items, total_paise, "
            "payment_method, status, shipment_status, tags) VALUES (:t,:r,:n,:p,:e,:li,:tot,'prepaid','payment_pending','not_shipped',:tags)",
            t=tenant_id, r=ref, n=name, p=phone_n, e=email_n, li=lines, tot=total, tags=["storefront", "cashfree"])
    base = settings.public_base_url.rstrip("/")
    body = {
        "order_id": ref, "order_amount": round(total / 100, 2), "order_currency": "INR",
        "customer_details": {"customer_id": "c" + phone_n[1:], "customer_phone": phone_n[3:], "customer_name": name,
                             **({"customer_email": email_n} if email_n else {})},
        "order_meta": {"return_url": f"{base}/store/{site_key}?order_id={ref}",
                       **({"notify_url": f"{base}/webhooks/cashfree"} if base.startswith("https://") else {})},
        "order_note": f"{srow['name']} storefront order"[:200],
        "order_tags": {"tenant_id": tenant_id, "store": srow["slug"]},
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(_api("/orders"), headers={**_headers(), "x-idempotency-key": ref}, json=body)
    if r.status_code >= 300:
        log.warning("cashfree create order %s failed: %s %s", ref, r.status_code, r.text[:300])
        async with tenant_session(tenant_id) as db:
            await db.exec("DELETE FROM commerce_orders WHERE order_ref=:r AND status='payment_pending'", r=ref)
        raise CheckoutError("The payment page couldn't be opened. Please try again.")
    d = r.json()
    return {"order_id": ref, "payment_session_id": d["payment_session_id"], "mode": "production" if settings.cashfree_env == "production" else "sandbox",
            "amount_inr": total / 100}


async def _mark_paid(db, order: dict, how: str) -> None:
    tags = sorted(set((order.get("tags") or []) + ["paid"] + (["demo_paid"] if how == "demo" else [])))
    await db.exec("UPDATE commerce_orders SET status='processing', placed_at=now(), expected_delivery=:eta, tags=:tags "
                  "WHERE order_ref=:r AND status='payment_pending'", eta=(now() + timedelta(days=5)).date(), tags=tags, r=order["order_ref"])
    # the shopper bought what was in the cart: stop cart reminders and take the stock
    await db.exec("UPDATE commerce_carts SET status='converted', converted_at=now(), updated_at=now() "
                  "WHERE customer_phone=:p AND status='open'", p=order["customer_phone"])
    for li in order["line_items"]:
        await db.exec("UPDATE commerce_products SET stock=greatest(stock - :q, 0) WHERE sku=:s", q=li["qty"], s=li["sku"])
    await audit(db, "customer", order["customer_name"], "order.paid", "order", order["order_ref"],
                {"total_inr": order["total_paise"] / 100, "via": how, "gateway": "cashfree"})


async def confirm(tenant_id: str, order_ref: str, *, from_webhook: bool = False) -> dict:
    """The shopper is back from the payment page (or Cashfree told us). Returns the order as the shop sees it."""
    ref = (order_ref or "").strip().upper()
    async with tenant_session(tenant_id) as db:
        order = await db.one("SELECT * FROM commerce_orders WHERE order_ref=:r AND 'cashfree' = ANY(tags)", r=ref)
    if not order:
        raise CheckoutError("We couldn't find that order.")
    if order["status"] == "payment_pending":
        try:
            cf_status = (await cashfree_order(ref)).get("order_status")
        except Exception as e:
            log.warning("cashfree status %s: %s", ref, e)
            cf_status = None
        how = "cashfree" if cf_status == "PAID" else None
        if not how and not from_webhook and settings.cashfree_env != "production" and settings.payments_confirm_on_return:
            how = "demo"  # sandbox demo: returning from the payment page is enough
        if how:
            async with tenant_session(tenant_id) as db:
                await _mark_paid(db, order, how)
                order = await db.one("SELECT * FROM commerce_orders WHERE order_ref=:r", r=ref)
    paid = order["status"] != "payment_pending"
    return {"order_id": order["order_ref"], "paid": paid, "status": order["status"], "demo": "demo_paid" in (order["tags"] or []),
            "total_inr": order["total_paise"] / 100, "items": [{"title": li["title"], "qty": li["qty"]} for li in order["line_items"]],
            "expected_delivery": order["expected_delivery"].isoformat() if order.get("expected_delivery") else None,
            "customer_name": order["customer_name"], "phone": order["customer_phone"]}


def webhook_ok(raw: bytes, timestamp: str | None, signature: str | None) -> bool:
    """Cashfree signs base64(HMAC-SHA256(secret, timestamp + raw body))."""
    if not (timestamp and signature and settings.cashfree_secret_key):
        return False
    mac = hmac.new(settings.cashfree_secret_key.encode(), timestamp.encode() + raw, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature)


async def on_webhook(payload: dict) -> dict:
    data = payload.get("data") or {}
    ref = (data.get("order") or {}).get("order_id")
    if not ref or payload.get("type") not in ("PAYMENT_SUCCESS_WEBHOOK", "PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK"):
        return {"ok": True, "ignored": True}
    if payload["type"] != "PAYMENT_SUCCESS_WEBHOOK":
        return {"ok": True}
    # the tenant isn't in the event body: read it back from the order's tags at Cashfree
    tenant_id = ((await cashfree_order(ref)).get("order_tags") or {}).get("tenant_id")
    if not tenant_id:
        return {"ok": False, "reason": "no tenant tag"}
    out = await confirm(tenant_id, ref, from_webhook=True)
    return {"ok": True, "paid": out["paid"]}

"""Commerce adapters: one "Commerce Contract" (find customer orders, get order, track, search products, check stock,
return/exchange, cancel, refund, address change, coupon) translated to each platform. The AI only ever calls the
contract; the tenant's `integrations` row (kind='commerce') picks the adapter.

- `mock`: a store living in our own DB tables (commerce_*), seeded per tenant (also the demo sandbox).
- `shopify`: Shopify Admin GraphQL (config: shop_domain, access_token, api_version).
- `woocommerce`: WooCommerce REST v3 (config: site_url, consumer_key, consumer_secret).
- `custom`: the business's own website. Reads use orders/products their site pushed to us as events (level 2);
  live reads and actions call ONE signed endpoint on their server that follows our spec (level 3).
"""

import re
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core import new_id, norm_phone


def _order_view(o: dict) -> dict:
    delivered = o.get("delivered_at")
    days = None
    if delivered:
        days = (datetime.now(timezone.utc) - delivered).total_seconds() / 86400
    return {
        "order_ref": o["order_ref"],
        "customer_name": o["customer_name"],
        "customer_phone": o.get("customer_phone"),
        "customer_email": o.get("customer_email"),
        "line_items": o["line_items"],
        "total_inr": o["total_paise"] / 100,
        "payment_method": o["payment_method"],
        "status": o["status"],
        "courier": o.get("courier"),
        "awb": o.get("awb"),
        "shipment_status": o.get("shipment_status"),
        "expected_delivery": o["expected_delivery"].isoformat() if o.get("expected_delivery") else None,
        "delivered_at": delivered.isoformat() if delivered else None,
        "days_since_delivery": round(days, 1) if days is not None else None,
        "refunded_inr": (o.get("refunded_paise") or 0) / 100,
        "tags": o.get("tags") or [],
        "placed_at": o["placed_at"].isoformat() if o.get("placed_at") else None,
        "shipping_address": o.get("shipping_address") or {},
    }


class MockCommerce:
    name = "mock"

    def __init__(self, db):
        self.db = db

    async def find_orders(self, phone: str | None = None, email: str | None = None, limit: int = 5) -> list[dict]:
        rows = await self.db.all(
            "SELECT * FROM commerce_orders WHERE (CAST(:p AS text) IS NOT NULL AND customer_phone=:p) "
            "OR (CAST(:e AS text) IS NOT NULL AND lower(customer_email)=:e) ORDER BY placed_at DESC LIMIT :l",
            p=norm_phone(phone), e=(email or None) and email.lower(), l=limit,
        )
        return [_order_view(r) for r in rows]

    async def get_order(self, order_ref: str) -> dict | None:
        ref = order_ref.strip().lstrip("#").upper()
        r = await self.db.one("SELECT * FROM commerce_orders WHERE upper(order_ref)=:r", r=ref)
        return _order_view(r) if r else None

    async def get_product(self, sku: str) -> dict | None:
        r = await self.db.one("SELECT * FROM commerce_products WHERE upper(sku)=upper(:s)", s=sku)
        if not r:
            return None
        return {
            "sku": r["sku"], "title": r["title"], "description": r["description"], "category": r["category"],
            "price_inr": r["price_paise"] / 100, "stock": r["stock"], "image_uri": r["image_uri"],
            "image_phash": r["image_phash"], "attributes": r["attributes"],
        }

    async def list_products(self) -> list[dict]:
        rows = await self.db.all("SELECT sku FROM commerce_products ORDER BY title")
        return [await self.get_product(r["sku"]) for r in rows]

    async def search_products(self, query: str, limit: int = 5) -> list[dict]:
        """Live catalogue search: every word scores against title, category and description."""
        words = [w for w in query.lower().replace(",", " ").split() if len(w) > 2][:8]
        rows = await self.db.all("SELECT sku, title, description, category, price_paise, stock FROM commerce_products")
        def score(r):
            hay = f"{r['title']} {r['category']} {r['description']}".lower()
            return sum(3 if w in r["title"].lower() else 1 for w in words if w in hay)
        ranked = sorted(rows, key=lambda r: -score(r))
        hits = [r for r in ranked if score(r) > 0][:limit] or ranked[:limit]
        return [{"sku": r["sku"], "title": r["title"], "price_inr": r["price_paise"] / 100, "in_stock": r["stock"] > 0,
                 "stock": r["stock"], "category": r["category"], "description": r["description"][:240]} for r in hits]

    async def check_stock(self, sku: str) -> dict:
        p = await self.get_product(sku)
        return {"sku": sku, "available": p["stock"] if p else 0, "found": bool(p)}

    # ---- mutations -------------------------------------------------------
    async def create_replacement(self, order_ref: str, skus: list[str], reason: str) -> dict:
        orig = await self.db.one("SELECT * FROM commerce_orders WHERE upper(order_ref)=upper(:r)", r=order_ref)
        items = [li for li in orig["line_items"] if not skus or li["sku"] in skus]
        new_ref = f"{orig['order_ref']}-R{secrets.randbelow(90) + 10}"
        await self.db.exec(
            "INSERT INTO commerce_orders (tenant_id, order_ref, customer_name, customer_phone, customer_email, line_items, "
            "total_paise, payment_method, status, shipping_address, courier, shipment_status, expected_delivery, tags) "
            "VALUES (:t, :ref, :n, :p, :e, :li, 0, 'replacement', 'processing', :addr, :c, 'pending_pickup', "
            "current_date + 3, ARRAY['replacement'])",
            t=self.db.tenant_id, ref=new_ref, n=orig["customer_name"], p=orig["customer_phone"],
            e=orig["customer_email"], li=[{**li, "price_paise": 0} for li in items], addr=orig["shipping_address"],
            c=orig["courier"],
        )
        for li in items:
            await self.db.exec("UPDATE commerce_products SET stock=greatest(stock-:q,0) WHERE sku=:s", q=li.get("qty", 1), s=li["sku"])
        await self.db.exec(
            "UPDATE commerce_orders SET tags=array_append(tags, :tag) WHERE id=:id", tag=f"replaced_by:{new_ref}", id=orig["id"]
        )
        return {"replacement_order_ref": new_ref, "eta_days": 3, "items": [li["title"] for li in items]}

    async def refund(self, order_ref: str, amount_inr: float, reason: str) -> dict:
        await self.db.exec(
            "UPDATE commerce_orders SET refunded_paise=refunded_paise+:a, tags=array_append(tags,'refunded') WHERE upper(order_ref)=upper(:r)",
            a=int(amount_inr * 100), r=order_ref,
        )
        return {"refund_id": f"RF-{new_id()[:8].upper()}", "amount_inr": amount_inr, "eta": "5-7 business days"}

    async def cancel(self, order_ref: str, reason: str) -> dict:
        await self.db.exec("UPDATE commerce_orders SET status='cancelled' WHERE upper(order_ref)=upper(:r)", r=order_ref)
        return {"cancelled": True}

    async def update_address(self, order_ref: str, address: dict) -> dict:
        await self.db.exec(
            "UPDATE commerce_orders SET shipping_address=:a WHERE upper(order_ref)=upper(:r)", a=address, r=order_ref
        )
        return {"updated": True, "address": address}

    async def create_discount(self, value_inr: float, customer_ref: str) -> dict:
        code = f"SORRY{secrets.randbelow(9000) + 1000}"
        await self.db.exec(
            "INSERT INTO commerce_discounts (tenant_id, code, value_paise, customer_ref) VALUES (:t, :c, :v, :r)",
            t=self.db.tenant_id, c=code, v=int(value_inr * 100), r=customer_ref,
        )
        return {"code": code, "value_inr": value_inr}

    async def tag_order(self, order_ref: str, tag: str) -> dict:
        await self.db.exec(
            "UPDATE commerce_orders SET tags=array_append(tags,:g) WHERE upper(order_ref)=upper(:r)", g=tag, r=order_ref
        )
        return {"tagged": tag}

    async def reschedule(self, order_ref: str, date: str) -> dict:
        await self.db.exec(
            "UPDATE commerce_orders SET expected_delivery=CAST(:d AS date), shipment_status='rescheduled' WHERE upper(order_ref)=upper(:r)",
            d=date, r=order_ref,
        )
        return {"rescheduled_to": date}


class ShopifyCommerce(MockCommerce):
    """Shopify Admin GraphQL. Reads map Shopify orders into our order view; mutations use
    standard Admin API mutations. Requires a custom app token with orders/products scopes."""

    name = "shopify"

    def __init__(self, db, config: dict):
        super().__init__(db)
        self.shop = config["shop_domain"]
        self.token = config["access_token"]
        self.version = config.get("api_version", "2026-07")

    async def _gql(self, query: str, variables: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"https://{self.shop}/admin/api/{self.version}/graphql.json",
                json={"query": query, "variables": variables or {}},
                headers={"X-Shopify-Access-Token": self.token},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            return data["data"]

    _ORDER_FIELDS = """
      name email phone displayFinancialStatus displayFulfillmentStatus createdAt
      totalPriceSet { shopMoney { amount } } paymentGatewayNames
      customer { displayName phone email }
      lineItems(first: 20) { nodes { sku title quantity originalUnitPriceSet { shopMoney { amount } } } }
      fulfillments(first: 5) { status deliveredAt trackingInfo { company number } }
      shippingAddress { address1 address2 city province zip country phone }
      tags
    """

    def _map(self, o: dict) -> dict:
        f = (o.get("fulfillments") or [{}])[0] if o.get("fulfillments") else {}
        delivered = f.get("deliveredAt")
        delivered_dt = datetime.fromisoformat(delivered.replace("Z", "+00:00")) if delivered else None
        gateways = " ".join(o.get("paymentGatewayNames") or []).lower()
        row = {
            "order_ref": o["name"].lstrip("#"),
            "customer_name": (o.get("customer") or {}).get("displayName") or "",
            "customer_phone": o.get("phone") or (o.get("customer") or {}).get("phone"),
            "customer_email": o.get("email"),
            "line_items": [
                {"sku": li.get("sku") or "", "title": li["title"], "qty": li["quantity"],
                 "price_paise": int(float(li["originalUnitPriceSet"]["shopMoney"]["amount"]) * 100)}
                for li in o["lineItems"]["nodes"]
            ],
            "total_paise": int(float(o["totalPriceSet"]["shopMoney"]["amount"]) * 100),
            "payment_method": "cod" if "cash" in gateways or "cod" in gateways else "prepaid",
            "status": (o.get("displayFulfillmentStatus") or "").lower(),
            "courier": ((f.get("trackingInfo") or [{}])[0] or {}).get("company") if f else None,
            "awb": ((f.get("trackingInfo") or [{}])[0] or {}).get("number") if f else None,
            "shipment_status": (f.get("status") or "").lower() if f else None,
            "expected_delivery": None,
            "delivered_at": delivered_dt,
            "tags": o.get("tags") or [],
            "placed_at": datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")),
            "shipping_address": o.get("shippingAddress") or {},
        }
        return _order_view(row)

    async def get_order(self, order_ref: str) -> dict | None:
        q = f'query($q:String!){{ orders(first:1, query:$q){{ nodes {{ {self._ORDER_FIELDS} }} }} }}'
        data = await self._gql(q, {"q": f"name:#{order_ref.lstrip('#')}"})
        nodes = data["orders"]["nodes"]
        return self._map(nodes[0]) if nodes else None

    async def find_orders(self, phone: str | None = None, email: str | None = None, limit: int = 5) -> list[dict]:
        term = f"email:{email}" if email else f"phone:{norm_phone(phone)}"
        q = f'query($q:String!,$n:Int!){{ orders(first:$n, sortKey:CREATED_AT, reverse:true, query:$q){{ nodes {{ {self._ORDER_FIELDS} }} }} }}'
        data = await self._gql(q, {"q": term, "n": limit})
        return [self._map(o) for o in data["orders"]["nodes"]]


    _PRODUCT_FIELDS = "title handle description productType totalInventory variants(first:1){ nodes { sku price inventoryQuantity } }"

    def _map_product(self, p: dict) -> dict:
        v = (p.get("variants") or {}).get("nodes") or [{}]
        stock = p.get("totalInventory") if p.get("totalInventory") is not None else (v[0].get("inventoryQuantity") or 0)
        return {"sku": v[0].get("sku") or p.get("handle"), "title": p["title"], "price_inr": float(v[0].get("price") or 0),
                "in_stock": stock > 0, "stock": stock, "category": p.get("productType") or "general",
                "description": (p.get("description") or "")[:240]}

    async def search_products(self, query: str, limit: int = 5) -> list[dict]:
        q = f'query($q:String!,$n:Int!){{ products(first:$n, query:$q){{ nodes {{ {self._PRODUCT_FIELDS} }} }} }}'
        data = await self._gql(q, {"q": query, "n": limit})
        return [self._map_product(p) for p in data["products"]["nodes"]]

    async def get_product(self, sku: str) -> dict | None:
        q = f'query($q:String!){{ productVariants(first:1, query:$q){{ nodes {{ product {{ {self._PRODUCT_FIELDS} }} }} }} }}'
        data = await self._gql(q, {"q": f"sku:{sku}"})
        nodes = data["productVariants"]["nodes"]
        if not nodes:
            return await super().get_product(sku)
        p = self._map_product(nodes[0]["product"])
        return {**p, "image_uri": None, "image_phash": None, "attributes": {}}


    async def abandoned_checkouts(self, limit: int = 25) -> list[dict]:
        q = ("query($n:Int!){ abandonedCheckouts(first:$n, sortKey:CREATED_AT, reverse:true){ nodes { id abandonedCheckoutUrl createdAt updatedAt "
             "customer { displayName phone email } lineItems(first:10){ nodes { title quantity sku } } totalPriceSet { shopMoney { amount } } } } }")
        data = await self._gql(q, {"n": limit})
        out = []
        for c in data["abandonedCheckouts"]["nodes"]:
            cu = c.get("customer") or {}
            out.append({"cart_ref": c["id"], "customer_name": cu.get("displayName"), "customer_phone": cu.get("phone"), "customer_email": cu.get("email"),
                        "items": [{"sku": li.get("sku"), "title": li["title"], "qty": li["quantity"]} for li in c["lineItems"]["nodes"]],
                        "total_paise": int(float(c["totalPriceSet"]["shopMoney"]["amount"]) * 100), "checkout_url": c.get("abandonedCheckoutUrl"),
                        "created_at": datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00")),
                        "updated_at": datetime.fromisoformat(c["updatedAt"].replace("Z", "+00:00"))})
        return out


class NotSupported(Exception):
    """The business's platform can't do this action automatically; the agent hands it to a human."""


# ---------------------------------------------------------------------------
# Custom website (the business's own stack)
# ---------------------------------------------------------------------------
class CustomCommerce(MockCommerce):
    """Level 2: orders/products mirrored from their events into our commerce_* tables (read with MockCommerce).
    Level 3: their endpoint (config.endpoint_url) answers contract actions; every request is signed:
        X-Sahay-Timestamp: <unix seconds>
        X-Sahay-Signature: sha256=HMAC_SHA256(secret_key, "<timestamp>.<raw body>")
        body: {"action": "getOrder", "params": {...}, "request_id": "..."}  →  {"ok": true, "data": {...}}
    Unknown actions should return {"ok": false, "error": "not_supported"}."""

    name = "custom"

    def __init__(self, db, config: dict, secret: str):
        super().__init__(db)
        self.url = (config.get("endpoint_url") or "").strip()
        self.secret = secret
        self.actions = set(config.get("actions") or [])  # discovered by a ping; empty = try everything

    async def call(self, action: str, params: dict, timeout: float = 8) -> Any:
        import hashlib
        import hmac
        import json
        import time

        if not self.url:
            raise NotSupported(f"{action}: no endpoint connected")
        if self.actions and action not in self.actions and action != "ping":
            raise NotSupported(f"{action}: not implemented on this website")
        body = json.dumps({"action": action, "params": params, "request_id": new_id()}, default=str)
        ts = str(int(time.time()))
        sig = "sha256=" + hmac.new(self.secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(self.url, content=body, headers={"Content-Type": "application/json", "X-Sahay-Timestamp": ts,
                                                              "X-Sahay-Signature": sig, "X-Sahay-Action": action})
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 404 or data.get("error") == "not_supported":
            raise NotSupported(f"{action}: not implemented on this website")
        if r.status_code >= 400 or not data.get("ok", False):
            raise RuntimeError(f"{action} failed on the website: {data.get('error') or r.status_code}")
        return data.get("data")

    @staticmethod
    def _order(o: dict) -> dict:
        """Their order (contract shape) → our order view."""
        def ts(v):
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
        return _order_view({
            "order_ref": str(o.get("orderRef") or o.get("order_ref") or o.get("id")), "customer_name": o.get("customerName") or "",
            "customer_phone": norm_phone(o.get("customerPhone")), "customer_email": (o.get("customerEmail") or "").lower() or None,
            "line_items": [{"sku": i.get("sku"), "title": i.get("title"), "qty": int(i.get("qty") or 1),
                            "price_paise": int(round(float(i.get("price") or 0) * 100))} for i in o.get("items") or []],
            "total_paise": int(round(float(o.get("total") or 0) * 100)), "payment_method": (o.get("paymentMethod") or "prepaid").lower(),
            "status": (o.get("status") or "processing").lower(), "courier": o.get("courier"), "awb": o.get("trackingNumber"),
            "shipment_status": o.get("shipmentStatus"), "expected_delivery": ts(o.get("expectedDelivery")) and ts(o.get("expectedDelivery")).date(),
            "delivered_at": ts(o.get("deliveredAt")), "tags": o.get("tags") or [], "placed_at": ts(o.get("placedAt")),
            "refunded_paise": int(round(float(o.get("refunded") or 0) * 100)), "shipping_address": o.get("shippingAddress") or {},
        })

    async def _live_or_mirror(self, action: str, params: dict, fallback):
        try:
            return await self.call(action, params)
        except NotSupported:
            return await fallback()

    async def get_order(self, order_ref: str) -> dict | None:
        async def mirror():
            return await MockCommerce.get_order(self, order_ref)
        o = await self._live_or_mirror("getOrder", {"orderRef": order_ref.strip().lstrip("#")}, mirror)
        return o if (o is None or "order_ref" in o) else self._order(o)

    async def find_orders(self, phone: str | None = None, email: str | None = None, limit: int = 5) -> list[dict]:
        async def mirror():
            return await MockCommerce.find_orders(self, phone, email, limit)
        rows = await self._live_or_mirror("findOrders", {"phone": norm_phone(phone), "email": email, "limit": limit}, mirror)
        return [r if "order_ref" in r else self._order(r) for r in rows or []]

    async def search_products(self, query: str, limit: int = 5) -> list[dict]:
        async def mirror():
            return await MockCommerce.search_products(self, query, limit)
        rows = await self._live_or_mirror("searchProducts", {"query": query, "limit": limit}, mirror)
        return [r if "price_inr" in r else {"sku": r.get("sku"), "title": r.get("title"), "price_inr": float(r.get("price") or 0),
                                            "in_stock": (r.get("stock") or 0) > 0, "stock": r.get("stock") or 0,
                                            "category": r.get("category") or "general", "description": (r.get("description") or "")[:240]}
                for r in rows or []]

    async def check_stock(self, sku: str) -> dict:
        async def mirror():
            return await MockCommerce.check_stock(self, sku)
        r = await self._live_or_mirror("checkStock", {"sku": sku}, mirror)
        return r if "found" in r else {"sku": sku, "available": int(r.get("stock") or r.get("available") or 0), "found": True}

    async def create_replacement(self, order_ref: str, skus: list[str], reason: str) -> dict:
        return await self.call("createReturn", {"orderRef": order_ref, "skus": skus, "reason": reason, "type": "exchange"}) or {}

    async def refund(self, order_ref: str, amount_inr: float, reason: str) -> dict:
        return await self.call("refundOrder", {"orderRef": order_ref, "amount": amount_inr, "reason": reason}) or {}

    async def cancel(self, order_ref: str, reason: str) -> dict:
        return await self.call("cancelOrder", {"orderRef": order_ref, "reason": reason}) or {}

    async def update_address(self, order_ref: str, address: dict) -> dict:
        return await self.call("updateAddress", {"orderRef": order_ref, "address": address}) or {}

    async def create_discount(self, value_inr: float, customer_ref: str) -> dict:
        try:
            return await self.call("createCoupon", {"value": value_inr, "customerRef": customer_ref}) or {}
        except NotSupported:
            return await MockCommerce.create_discount(self, value_inr, customer_ref)

    async def tag_order(self, order_ref: str, tag: str) -> dict:
        if tag == "return_requested":
            return await self.call("createReturn", {"orderRef": order_ref, "skus": [], "reason": "customer request", "type": "return"}) or {}
        return await self.call("tagOrder", {"orderRef": order_ref, "tag": tag}) or {}

    async def reschedule(self, order_ref: str, date: str) -> dict:
        return await self.call("rescheduleDelivery", {"orderRef": order_ref, "date": date}) or {}


# ---------------------------------------------------------------------------
# WooCommerce (REST v3, consumer key + secret)
# ---------------------------------------------------------------------------
class WooCommerce(MockCommerce):
    name = "woocommerce"

    def __init__(self, db, config: dict):
        super().__init__(db)
        self.base = config["site_url"].rstrip("/") + "/wp-json/wc/v3"
        self.auth = (config["consumer_key"], config["consumer_secret"])

    async def _req(self, method: str, path: str, **kw) -> Any:
        async with httpx.AsyncClient(timeout=15, auth=self.auth) as c:
            r = await c.request(method, self.base + path, **kw)
            r.raise_for_status()
            return r.json()

    def _map(self, o: dict) -> dict:
        b, sh = o.get("billing") or {}, o.get("shipping") or {}
        meta = {m.get("key"): m.get("value") for m in o.get("meta_data") or []}
        def ts(v):
            return datetime.fromisoformat(v).replace(tzinfo=timezone.utc) if v else None
        status = {"completed": "delivered", "processing": "processing", "on-hold": "processing", "pending": "processing",
                  "cancelled": "cancelled", "refunded": "cancelled", "failed": "cancelled"}.get(o.get("status"), o.get("status"))
        return _order_view({
            "order_ref": str(o["number"] if o.get("number") else o["id"]), "customer_name": f"{b.get('first_name', '')} {b.get('last_name', '')}".strip(),
            "customer_phone": norm_phone(b.get("phone")), "customer_email": (b.get("email") or "").lower() or None,
            "line_items": [{"sku": li.get("sku") or str(li.get("product_id")), "title": li["name"], "qty": li["quantity"],
                            "price_paise": int(round(float(li.get("price") or 0) * 100))} for li in o.get("line_items") or []],
            "total_paise": int(round(float(o.get("total") or 0) * 100)),
            "payment_method": "cod" if o.get("payment_method") == "cod" else "prepaid", "status": status,
            "courier": meta.get("_tracking_provider") or meta.get("courier"), "awb": meta.get("_tracking_number") or meta.get("awb"),
            "shipment_status": None, "expected_delivery": None, "delivered_at": ts(o.get("date_completed_gmt")),
            "tags": [], "placed_at": ts(o.get("date_created_gmt")), "shipping_address": {"address": sh.get("address_1"), "city": sh.get("city"), "pincode": sh.get("postcode")},
            "refunded_paise": int(round(sum(abs(float(r.get("total") or 0)) for r in o.get("refunds") or []) * 100)),
        })

    async def get_order(self, order_ref: str) -> dict | None:
        try:
            return self._map(await self._req("GET", f"/orders/{order_ref.strip().lstrip('#')}"))
        except httpx.HTTPStatusError:
            return None

    async def find_orders(self, phone: str | None = None, email: str | None = None, limit: int = 5) -> list[dict]:
        term = email or (norm_phone(phone) or "")[-10:]
        rows = await self._req("GET", "/orders", params={"search": term, "per_page": limit, "orderby": "date", "order": "desc"})
        return [self._map(o) for o in rows]

    def _product(self, p: dict) -> dict:
        stock = p.get("stock_quantity") if p.get("stock_quantity") is not None else (99 if p.get("stock_status") == "instock" else 0)
        return {"sku": p.get("sku") or str(p["id"]), "title": p["name"], "price_inr": float(p.get("price") or 0), "in_stock": stock > 0,
                "stock": stock, "category": ((p.get("categories") or [{}])[0] or {}).get("name") or "general",
                "description": re.sub(r"<[^>]+>", "", p.get("short_description") or p.get("description") or "")[:240]}

    async def search_products(self, query: str, limit: int = 5) -> list[dict]:
        return [self._product(p) for p in await self._req("GET", "/products", params={"search": query, "per_page": limit})]

    async def get_product(self, sku: str) -> dict | None:
        rows = await self._req("GET", "/products", params={"sku": sku})
        if not rows:
            return await super().get_product(sku)
        return {**self._product(rows[0]), "image_uri": None, "image_phash": None, "attributes": {}}

    async def check_stock(self, sku: str) -> dict:
        p = await self.get_product(sku)
        return {"sku": sku, "available": (p or {}).get("stock", 0), "found": bool(p)}

    async def cancel(self, order_ref: str, reason: str) -> dict:
        await self._req("PUT", f"/orders/{order_ref}", json={"status": "cancelled", "customer_note": reason})
        return {"cancelled": True, "order_ref": order_ref}

    async def refund(self, order_ref: str, amount_inr: float, reason: str) -> dict:
        r = await self._req("POST", f"/orders/{order_ref}/refunds", json={"amount": f"{amount_inr:.2f}", "reason": reason, "api_refund": False})
        return {"refund_id": r.get("id"), "amount_inr": amount_inr, "note": "Refund recorded in WooCommerce; pay out via your gateway if not automatic."}

    async def update_address(self, order_ref: str, address: dict) -> dict:
        await self._req("PUT", f"/orders/{order_ref}", json={"shipping": {"address_1": address.get("address"), "postcode": address.get("pincode")}})
        return {"updated": True}

    async def tag_order(self, order_ref: str, tag: str) -> dict:
        await self._req("POST", f"/orders/{order_ref}/notes", json={"note": f"Sahay: {tag}", "customer_note": False})
        return {"order_ref": order_ref, "tag": tag}

    async def create_replacement(self, order_ref: str, skus: list[str], reason: str) -> dict:
        o = await self._req("GET", f"/orders/{order_ref}")
        items = [{"product_id": li["product_id"], "variation_id": li.get("variation_id") or 0, "quantity": li["quantity"], "total": "0"}
                 for li in o.get("line_items") or [] if not skus or (li.get("sku") or str(li.get("product_id"))) in skus]
        new = await self._req("POST", "/orders", json={"status": "processing", "billing": o.get("billing"), "shipping": o.get("shipping"),
                                                        "line_items": items, "customer_note": f"Free replacement for #{order_ref}: {reason}"})
        return {"replacement_order": str(new.get("number") or new.get("id")), "items": len(items)}

    async def reschedule(self, order_ref: str, date: str) -> dict:
        return await self.tag_order(order_ref, f"reschedule delivery to {date}")


async def get_commerce(db) -> MockCommerce:
    from app.core import unseal

    row = await db.one("SELECT config FROM integrations WHERE kind='commerce' AND status='active'")
    cfg: dict[str, Any] = unseal((row or {}).get("config") or {})
    if cfg.get("provider") == "shopify" and cfg.get("access_token"):
        return ShopifyCommerce(db, cfg)
    if cfg.get("provider") == "woocommerce" and cfg.get("consumer_key") and cfg.get("site_url"):
        return WooCommerce(db, cfg)
    if cfg.get("provider") == "custom":
        secret = await db.val("SELECT secret_key FROM agent_profiles") or ""
        return CustomCommerce(db, cfg, secret)
    return MockCommerce(db)

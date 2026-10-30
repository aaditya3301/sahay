"""Public endpoints for the embeddable widget, Reply Now chat links and the voice pipeline.

How the server decides the business (never the AI, never the browser):
  widget       → public site key + the parent site's domain (allow-list, and CSP frame-ancestors on the iframe)
  Reply Now    → signed link carrying business + lead + expiry
  voice        → the business's secret key (or the platform key + the dialled number)
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.config import ROOT, settings
from app.core import audit, jsonable, norm_email, norm_phone, now
from app.db import system_session, tenant_session
from app.pipeline import InboundAttachment, InboundMessage, ingest, resolve_customer
from app.profile import load_profile, verify_user_hash

router = APIRouter()
WIDGET_DIR = ROOT / "apps" / "widget"


# ---------------------------------------------------------------------------
# Rate limits (single instance → in memory)
# ---------------------------------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)


def limit(request: Request, bucket: str, per_minute: int) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    key, t = f"{bucket}|{ip}", time.time()
    q = _hits[key]
    while q and q[0] < t - 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(429, "Too many requests, slow down a little.")
    q.append(t)


# ---------------------------------------------------------------------------
# Bot check: a small proof-of-work every new chat session must solve (~0.2s in a browser, costly at bot scale),
# or Cloudflare Turnstile when its keys are set. Plus per-session and per-business daily message caps,
# which also cap what a bot can spend on AI calls.
# ---------------------------------------------------------------------------
_used_challenges: dict[str, float] = {}


def _sign(data: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:32]


@router.get("/v1/widget/challenge")
async def widget_challenge(request: Request):
    limit(request, "challenge", 30)
    body = f"{int(time.time()) + 300}.{secrets.token_hex(8)}.{settings.widget_pow_bits}"
    return {"challenge": f"{body}.{_sign(body)}", "bits": settings.widget_pow_bits, "turnstile_site_key": settings.turnstile_site_key or None}


def _pow_ok(challenge: str | None, counter: str | None) -> bool:
    try:
        exp, rnd, bits, sig = (challenge or "").split(".")
        body = f"{exp}.{rnd}.{bits}"
        if not hmac.compare_digest(sig, _sign(body)) or int(exp) < time.time() or challenge in _used_challenges:
            return False
        digest = hashlib.sha256(f"{challenge}:{counter}".encode()).digest()
        value = int.from_bytes(digest, "big")
        if value >> (256 - int(bits)) != 0:
            return False
    except Exception:
        return False
    now_ = time.time()
    for k in [k for k, v in _used_challenges.items() if v < now_]:
        _used_challenges.pop(k, None)
    _used_challenges[challenge] = now_ + 600
    return True


async def _turnstile_ok(token: str | None, ip: str | None) -> bool:
    import httpx

    if not token:
        return False
    async with httpx.AsyncClient(timeout=6) as c:
        r = await c.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                         data={"secret": settings.turnstile_secret, "response": token, "remoteip": ip or ""})
    return bool(r.json().get("success"))


async def _caps_ok(db, tid: str, visitor: str, widget_cfg: dict) -> str | None:
    row = await db.one(
        "SELECT count(*) FILTER (WHERE m.created_at > now() - interval '24 hours') AS tenant_day, "
        "count(*) FILTER (WHERE i.value=:v AND m.created_at > now() - interval '24 hours') AS session_day "
        "FROM messages m LEFT JOIN customer_identities i ON i.customer_id=m.customer_id AND i.kind='webchat_visitor' "
        "WHERE m.channel='webchat' AND m.direction='in'", v=visitor)
    if row["session_day"] >= int(widget_cfg.get("session_message_cap") or 40):
        return "You've sent a lot of messages today. Please reach us on WhatsApp or email and the team will help."
    if row["tenant_day"] >= int(widget_cfg.get("daily_message_cap") or 400):
        return "Our chat is very busy right now. Please message us on WhatsApp or email and we'll get back to you."
    return None


# ---------------------------------------------------------------------------
# Static: loader script + chat page
# ---------------------------------------------------------------------------
async def _site_tenant(key: str) -> str:
    async with system_session() as db:
        r = await db.one("SELECT tenant_id FROM channel_routes WHERE channel='webchat' AND external_key=:k AND active", k=key)
    if not r:
        raise HTTPException(404, "unknown site key")
    return str(r["tenant_id"])


def _own_hosts() -> set[str]:
    return {urlparse(settings.public_base_url).hostname or "", "localhost", "127.0.0.1"}


def _domain_ok(origin: str | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    host = (urlparse(origin or "").hostname or "").lower()
    if not host:
        return False
    if host in _own_hosts():
        return True  # our own demo storefront + playground
    for d in allowed:
        d = d.lower().strip().removeprefix("https://").removeprefix("http://").strip("/")
        if d.startswith("*.") and (host.endswith(d[1:]) or host == d[2:]):
            return True
        if host == d or host == "www." + d:
            return True
    return False


@router.get("/widget.js")
@router.get("/loader.js")
async def loader_js():
    return FileResponse(WIDGET_DIR / "widget.js", media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})


@router.get("/w/{key}")
async def chat_page(key: str):
    tid = await _site_tenant(key)
    async with tenant_session(tid) as db:
        prof = await load_profile(db)
    allowed = [d.strip() for d in prof["widget"].get("allowed_domains") or [] if d.strip()]
    sources = " ".join(["'self'"] + [f"https://{d.removeprefix('https://').removeprefix('http://').strip('/')}" for d in allowed]
                       + [f"https://www.{d.removeprefix('https://').strip('/')}" for d in allowed if not d.startswith("*.")])
    csp = f"frame-ancestors {sources}" if allowed else "frame-ancestors *"
    return HTMLResponse((WIDGET_DIR / "chat.html").read_text(encoding="utf-8"), headers={"Content-Security-Policy": csp, "Cache-Control": "no-cache"})


@router.get("/r/{token}")
async def reply_now_page(token: str):
    return HTMLResponse((WIDGET_DIR / "chat.html").read_text(encoding="utf-8"),
                        headers={"Content-Security-Policy": "frame-ancestors 'none'", "Cache-Control": "no-cache"})


@router.get("/v1/widget/config")
async def widget_config(key: str):
    tid = await _site_tenant(key)
    async with tenant_session(tid) as db:
        s = await db.one("SELECT t.name, t.slug, s.brand_color, s.widget_greeting FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        prof = await load_profile(db)
    return {**s, "agent_name": prof["agent_name"], "launcher_text": prof["widget"].get("launcher_text")}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _session_token(tid: str, visitor: str, origin: str | None, verified: bool, lead_id: str | None = None) -> str:
    return jwt.encode({"typ": "ws", "t": tid, "v": visitor, "o": origin, "vf": verified, "l": lead_id,
                       "exp": int(time.time()) + 7 * 86400}, settings.jwt_secret, "HS256")


def _read_session(token: str) -> dict:
    try:
        d = jwt.decode(token, settings.jwt_secret, ["HS256"])
    except Exception:
        raise HTTPException(401, "chat session expired, please reload")
    if d.get("typ") != "ws":
        raise HTTPException(401, "bad session")
    return d


class SessionBody(BaseModel):
    pow_challenge: str | None = None
    pow_counter: str | None = None
    turnstile_token: str | None = None
    key: str | None = None
    reply_token: str | None = None
    visitor_id: str | None = None
    parent_origin: str | None = None
    user: dict | None = None
    user_hash: str | None = None


@router.post("/v1/widget/session")
async def widget_session(body: SessionBody, request: Request):
    from app.sales import read_reply_token

    limit(request, "session", 30)
    lead = None
    if body.reply_token:
        parsed = read_reply_token(body.reply_token)
        if not parsed:
            raise HTTPException(401, "This chat link has expired. Reply to the email and we'll send a fresh one.")
        tid, lead_id = parsed
        visitor = f"lead-{lead_id}"
    else:
        if not body.key:
            raise HTTPException(400, "missing site key")
        tid = await _site_tenant(body.key)
        visitor = (body.visitor_id or "")[:80] or f"v_{secrets.token_hex(8)}"
        lead_id = None
    async with tenant_session(tid) as db:
        prof = await load_profile(db)
        s = await db.one("SELECT t.name, t.slug, s.brand_color, s.widget_greeting FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        if not body.reply_token and not _domain_ok(body.parent_origin, prof["widget"].get("allowed_domains") or []):
            raise HTTPException(403, "This chat widget is not enabled for this website.")
        if prof["widget"].get("bot_check", True) and not (body.visitor_id and await db.one(
                "SELECT 1 FROM customer_identities WHERE kind='webchat_visitor' AND value=:v", v=visitor)):  # returning visitors skip it
            ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or None
            human = (await _turnstile_ok(body.turnstile_token, ip)) if settings.turnstile_secret else _pow_ok(body.pow_challenge, body.pow_counter)
            if not human:
                raise HTTPException(403, "Couldn't verify this browser. Please reload the page.")
        verified, name, email, phone = False, None, None, None
        if lead_id:
            lead = await db.one("SELECT * FROM leads WHERE id=:id", id=lead_id)
            if not lead:
                raise HTTPException(404, "link no longer valid")
            name, email = lead["name"], lead["email"]
            verified = bool(email)  # they opened a link we sent to that inbox
        elif body.user:
            u = body.user
            email, phone, name = norm_email(u.get("email")), norm_phone(u.get("phone")), u.get("name")
            verified = verify_user_hash(prof.get("secret_key"), email or phone, body.user_hash)
            if not verified:
                email = phone = None  # unsigned contact details are not trusted for order access
        cust = await resolve_customer(db, InboundMessage(channel="webchat", visitor_id=visitor, from_email=email if verified else None,
                                                         from_phone=phone if verified else None, from_name=name, verified=verified))
        if lead and not lead["customer_id"]:
            await db.exec("UPDATE leads SET customer_id=:c WHERE id=:id", c=cust["id"], id=lead["id"])
        if lead:
            from app.sales import add_event

            await add_event(db, lead["id"], "link_opened", "Opened the Reply Now chat link")
            if lead["stage"] in ("new", "contacted", "qualified"):
                await db.exec("UPDATE leads SET stage='engaged', last_contact_at=now(), updated_at=now() WHERE id=:id", id=lead["id"])
        ids = await db.val("SELECT count(*) FROM customer_identities WHERE customer_id=:c AND verified AND kind IN ('phone','email')", c=cust["id"])
    greeting = s["widget_greeting"]
    if lead:
        greeting = f"Hi{' ' + (name or '').split(' ')[0] if name else ''}! Thanks for clicking through from our email 🙌 Ask me anything — products, bulk pricing, or setting up a call."
    return jsonable({
        "session": _session_token(tid, visitor, body.parent_origin, bool(ids), lead_id), "visitor_id": visitor, "verified": bool(ids),
        "mode": "reply_now" if lead else "widget", "customer_name": cust.get("display_name"),
        "config": {"name": s["name"], "brand_color": s["brand_color"], "greeting": greeting, "agent_name": prof["agent_name"],
                   "require_otp": prof["widget"].get("require_otp_for_orders", True)},
    })


@router.post("/v1/widget/send")
async def widget_send(request: Request, session: str = Form(...), text: str = Form(""), context: str = Form(""),
                      file: UploadFile | None = File(None)):
    limit(request, "send", 40)
    sess = _read_session(session)
    async with tenant_session(sess["t"]) as db:
        prof = await load_profile(db)
        blocked = await _caps_ok(db, sess["t"], sess["v"], prof["widget"])
    if blocked:
        raise HTTPException(429, blocked)
    try:
        ctx = json.loads(context) if context else None
    except Exception:
        ctx = None
    if ctx:  # keep it small and plain
        ctx = {k: ctx[k] for k in ("page_url", "page_title", "product_sku", "product_title", "product", "cart", "source") if k in ctx}
    atts = [InboundAttachment(await file.read(), file.content_type or "application/octet-stream", file.filename or "")] if file else []
    m = await ingest(sess["t"], InboundMessage(channel="webchat", text=text[:4000], visitor_id=sess["v"], attachments=atts, context=ctx))
    return {"ok": True, "message_id": m and str(m["id"])}


@router.get("/v1/widget/poll")
async def widget_poll(session: str):
    sess = _read_session(session)
    async with tenant_session(sess["t"]) as db:
        conv = await db.one(
            "SELECT c.id FROM conversations c JOIN customer_identities i ON i.customer_id=c.customer_id "
            "WHERE i.kind='webchat_visitor' AND i.value=:v AND c.channel='webchat' ORDER BY c.created_at DESC LIMIT 1", v=sess["v"])
        if not conv:
            return {"messages": [], "typing": False}
        msgs = await db.all(
            "SELECT m.id, m.direction, m.text, m.author_type, m.author_name, m.interactive, m.created_at, "
            "(SELECT json_agg(json_build_object('id', a.id, 'kind', a.kind)) FROM attachments a WHERE a.message_id=m.id) AS attachments "
            "FROM messages m WHERE m.conversation_id=:c ORDER BY m.created_at", c=conv["id"])
        pending = await db.val("SELECT count(*) FROM messages WHERE conversation_id=:c AND direction='in' AND triage IS NULL", c=conv["id"])
        running = await db.val("SELECT count(*) FROM agent_runs r JOIN messages m ON m.id=r.trigger_message_id WHERE m.conversation_id=:c "
                               "AND r.status='running' AND r.created_at > now()-interval '3 minutes'", c=conv["id"])
    return jsonable({"messages": msgs, "typing": bool(pending or running)})


# ---------------------------------------------------------------------------
# Phone OTP for anonymous visitors who want order details
# ---------------------------------------------------------------------------
_otps: dict[str, dict] = {}


class OtpStart(BaseModel):
    session: str
    phone: str


class OtpVerify(BaseModel):
    session: str
    phone: str
    code: str


@router.post("/v1/widget/otp/start")
async def otp_start(body: OtpStart, request: Request):
    limit(request, "otp", 5)
    sess = _read_session(body.session)
    phone = norm_phone(body.phone)
    if not phone or len(phone) < 12:
        raise HTTPException(400, "Please enter a valid 10-digit mobile number.")
    code = f"{secrets.randbelow(10**6):06d}"
    _otps[f"{sess['t']}|{sess['v']}"] = {"phone": phone, "code_hash": hashlib.sha256(code.encode()).hexdigest(), "exp": time.time() + 600, "tries": 0}
    from app.channels import integration, wa_send

    async with tenant_session(sess["t"]) as db:
        cfg = await integration(db, "whatsapp")
        name = await db.val("SELECT name FROM tenants WHERE id=:t", t=sess["t"])
        sent = False
        if cfg.get("access_token") and cfg.get("phone_number_id"):
            try:
                await wa_send(cfg, phone, {"type": "text", "text": {"body": f"{code} is your {name} verification code. It expires in 10 minutes."}})
                sent = True
            except Exception:
                sent = False
        await audit(db, "system", None, "widget.otp_sent", "visitor", sess["v"], {"phone": phone[-4:], "via": "whatsapp" if sent else "demo"})
    # Until WhatsApp/SMS is connected the code is returned so the demo works; never do this in production.
    return {"ok": True, "sent_via": "whatsapp" if sent else "demo", **({} if sent else {"demo_code": code})}


@router.post("/v1/widget/otp/verify")
async def otp_verify(body: OtpVerify, request: Request):
    limit(request, "otp", 10)
    sess = _read_session(body.session)
    k = f"{sess['t']}|{sess['v']}"
    rec = _otps.get(k)
    phone = norm_phone(body.phone)
    if not rec or rec["exp"] < time.time() or rec["phone"] != phone:
        raise HTTPException(400, "Code expired. Please request a new one.")
    rec["tries"] += 1
    if rec["tries"] > 5 or not hmac.compare_digest(rec["code_hash"], hashlib.sha256(body.code.strip().encode()).hexdigest()):
        raise HTTPException(400, "That code doesn't match.")
    _otps.pop(k, None)
    for attempt in range(3):  # merging touches many tables; retry if a live reply holds the same rows
        try:
            async with tenant_session(sess["t"]) as db:
                name = await verify_visitor_phone(db, sess["v"], phone)
                await audit(db, "system", None, "widget.phone_verified", "visitor", sess["v"], {"phone": phone[-4:]})
            break
        except Exception as e:
            if "deadlock" not in str(e).lower() or attempt == 2:
                raise
            await asyncio.sleep(1 + attempt)
    return {"ok": True, "session": _session_token(sess["t"], sess["v"], sess.get("o"), True, sess.get("l")), "customer_name": name}


MOVE_TABLES = ["conversations", "tickets", "messages", "actions", "fraud_signals", "leads", "voice_calls", "campaign_sends", "lifecycle_events"]


async def verify_visitor_phone(db, visitor: str, phone: str) -> str | None:
    """Prove the visitor owns this phone; if the store already knows that phone, merge the chat into that customer."""
    visitor_c = await db.one("SELECT c.* FROM customer_identities i JOIN customers c ON c.id=i.customer_id WHERE i.kind='webchat_visitor' AND i.value=:v", v=visitor)
    if not visitor_c:
        visitor_c = await resolve_customer(db, InboundMessage(channel="webchat", visitor_id=visitor))
    known = await db.one("SELECT c.* FROM customer_identities i JOIN customers c ON c.id=i.customer_id WHERE i.kind='phone' AND i.value=:p", p=phone)
    target = visitor_c
    if known and known["id"] != visitor_c["id"]:
        for t in MOVE_TABLES:
            await db.exec(f"UPDATE {t} SET customer_id=:k WHERE customer_id=:v", k=known["id"], v=visitor_c["id"])
        await db.exec("UPDATE customer_identities SET customer_id=:k WHERE customer_id=:v", k=known["id"], v=visitor_c["id"])
        await db.exec("DELETE FROM customers WHERE id=:v", v=visitor_c["id"])
        target = known
    await db.exec("INSERT INTO customer_identities (tenant_id, customer_id, kind, value, verified) VALUES (:t,:c,'phone',:p,true) "
                  "ON CONFLICT (tenant_id, kind, value) DO UPDATE SET verified=true", t=db.tenant_id, c=target["id"], p=phone)
    order = await db.one("SELECT customer_name, customer_email FROM commerce_orders WHERE customer_phone=:p ORDER BY placed_at DESC LIMIT 1", p=phone)
    await db.exec("UPDATE customers SET primary_phone=coalesce(primary_phone,:p), display_name=coalesce(display_name,:n), "
                  "primary_email=coalesce(primary_email,:e) WHERE id=:c", p=phone, n=(order or {}).get("customer_name"),
                  e=(order or {}).get("customer_email"), c=target["id"])
    if order and order.get("customer_email"):
        await db.exec("INSERT INTO customer_identities (tenant_id, customer_id, kind, value, verified) VALUES (:t,:c,'email',:e,true) "
                      "ON CONFLICT (tenant_id, kind, value) DO UPDATE SET verified=true", t=db.tenant_id, c=target["id"], e=order["customer_email"].lower())
    return target.get("display_name") or (order or {}).get("customer_name")


class CartBody(BaseModel):
    session: str
    items: list[dict] = []
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    checkout_url: str | None = None


@router.post("/v1/widget/cart")
async def widget_cart(body: CartBody, request: Request):
    """The shop reports its cart (Sahay('cart', ...)). Prices come from our catalogue, never the browser."""
    limit(request, "cart", 30)
    sess = _read_session(body.session)
    async with tenant_session(sess["t"]) as db:
        items, total = [], 0
        for it in body.items[:30]:
            p = await db.one("SELECT sku, title, price_paise FROM commerce_products WHERE upper(sku)=upper(:s)", s=str(it.get("sku") or ""))
            qty = max(1, min(int(it.get("qty") or 1), 50))
            if p:
                items.append({"sku": p["sku"], "title": p["title"], "qty": qty, "price_paise": p["price_paise"]})
                total += p["price_paise"] * qty
            elif it.get("title"):
                items.append({"sku": it.get("sku"), "title": str(it["title"])[:120], "qty": qty, "price_paise": 0})
        cust = await db.one("SELECT c.* FROM customer_identities i JOIN customers c ON c.id=i.customer_id WHERE i.kind='webchat_visitor' AND i.value=:v", v=sess["v"])
        phone = norm_phone(body.phone) or (cust or {}).get("primary_phone")
        email = norm_email(body.email) or (cust or {}).get("primary_email")
        url = body.checkout_url if (body.checkout_url or "").startswith("https://") else None
        row = await db.one(
            "INSERT INTO commerce_carts (tenant_id, cart_ref, customer_name, customer_phone, customer_email, items, total_paise, checkout_url, source) "
            "VALUES (:t,:r,:n,:p,:e,:i,:tot,:u,'widget') ON CONFLICT (tenant_id, cart_ref) DO UPDATE SET items=:i, total_paise=:tot, "
            "customer_name=coalesce(:n, commerce_carts.customer_name), customer_phone=coalesce(:p, commerce_carts.customer_phone), "
            "customer_email=coalesce(:e, commerce_carts.customer_email), checkout_url=coalesce(:u, commerce_carts.checkout_url), "
            "status=CASE WHEN commerce_carts.status IN ('converted','expired') OR :i = '[]'::jsonb THEN commerce_carts.status ELSE 'open' END, updated_at=now() RETURNING id, status",
            t=sess["t"], r=f"web-{sess['v']}", n=body.name or (cust or {}).get("display_name"), p=phone, e=email, i=items, tot=total, u=url)
    return jsonable({"ok": True, "cart_id": row["id"], "items": len(items), "total_inr": total / 100})


@router.get("/u/{token}")
async def unsubscribe(token: str):
    try:
        d = jwt.decode(token, settings.jwt_secret, ["HS256"])
        assert d.get("typ") == "unsub"
    except Exception:
        return HTMLResponse("<p style='font-family:sans-serif'>This link is not valid.</p>", status_code=400)
    async with tenant_session(d["t"]) as db:
        await db.exec("UPDATE customers SET marketing_opt_out=true WHERE id=:c", c=d["c"])
        name = await db.val("SELECT name FROM tenants WHERE id=:t", t=d["t"])
        await audit(db, "customer", None, "marketing.opt_out", "customer", d["c"], {"via": "email_link"})
    return HTMLResponse(f"<div style='font-family:system-ui,sans-serif;max-width:480px;margin:80px auto;text-align:center'>"
                        f"<h2>You're unsubscribed</h2><p>You won't get newsletters or offers from {name} anymore. "
                        f"Order updates and support replies will still reach you.</p></div>")


@router.get("/v1/widget/products")
async def widget_products(key: str):
    tid = await _site_tenant(key)
    async with tenant_session(tid) as db:
        slug = await db.val("SELECT slug FROM tenants WHERE id=:t", t=tid)
        rows = await db.all("SELECT sku, title, description, price_paise, stock, image_uri IS NOT NULL AS has_image FROM commerce_products ORDER BY title LIMIT 12")
    return [{"sku": r["sku"], "title": r["title"], "description": r["description"], "price_inr": r["price_paise"] / 100, "in_stock": r["stock"] > 0,
             "image": f"/public/products/{slug}/{r['sku']}.png" if r["has_image"] else None} for r in rows]


# ---------------------------------------------------------------------------
# Voice pipeline API (Naaradh): authenticated with the business's secret key
# ---------------------------------------------------------------------------
async def voice_tenant(x_sahay_key: str | None, x_platform_key: str | None = None, to_number: str | None = None) -> str:
    if x_sahay_key:
        async with system_session() as db:
            r = await db.one("SELECT tenant_id FROM channel_routes WHERE channel='secret' AND external_key=:k",
                             k=hashlib.sha256(x_sahay_key.encode()).hexdigest())
        if r:
            return str(r["tenant_id"])
    if x_platform_key and settings.voice_platform_key and hmac.compare_digest(x_platform_key, settings.voice_platform_key) and to_number:
        async with system_session() as db:
            r = await db.one("SELECT tenant_id FROM channel_routes WHERE channel='voice' AND external_key=:k", k=norm_phone(to_number))
        if r:
            return str(r["tenant_id"])
    raise HTTPException(401, "unknown business key or number")


class CallStart(BaseModel):
    direction: str = "inbound"
    from_number: str | None = None
    to_number: str | None = None
    purpose: str = "inbound"
    call_id: str | None = None


class CallTurn(BaseModel):
    text: str


class CallTool(BaseModel):
    name: str
    args: dict = {}


class CallEnd(BaseModel):
    transcript: list[dict] | None = None
    status: str = "completed"


@router.post("/v1/voice/calls/start")
async def voice_start(body: CallStart, x_sahay_key: str | None = Header(None), x_platform_key: str | None = Header(None)):
    from app import voice

    tid = await voice_tenant(x_sahay_key, x_platform_key, body.to_number if body.direction == "inbound" else None)
    return jsonable(await voice.start_call(tid, direction=body.direction, from_number=body.from_number, to_number=body.to_number,
                                           purpose=body.purpose, call_id=body.call_id))


@router.post("/v1/voice/calls/{call_id}/turn")
async def voice_turn(call_id: str, body: CallTurn, x_sahay_key: str | None = Header(None)):
    from app import voice

    return jsonable(await voice.turn(await voice_tenant(x_sahay_key), call_id, body.text))


@router.post("/v1/voice/calls/{call_id}/tool")
async def voice_tool(call_id: str, body: CallTool, x_sahay_key: str | None = Header(None)):
    from app import voice

    return jsonable(await voice.run_tool(await voice_tenant(x_sahay_key), call_id, body.name, body.args))


@router.post("/v1/voice/calls/{call_id}/end")
async def voice_end(call_id: str, body: CallEnd, x_sahay_key: str | None = Header(None)):
    from app import voice

    return jsonable(await voice.end_call(await voice_tenant(x_sahay_key), call_id, body.transcript, body.status))


@router.get("/v1/voice/tasks")
async def voice_tasks(x_sahay_key: str | None = Header(None)):
    tid = await voice_tenant(x_sahay_key)
    async with tenant_session(tid) as db:
        rows = await db.all("SELECT id, purpose, to_number, context, scheduled_for FROM voice_calls WHERE status='queued' AND direction='outbound' "
                            "AND scheduled_for <= now() ORDER BY scheduled_for LIMIT 20")
    return jsonable({"calls": rows, "checked_at": now()})


# ---------------------------------------------------------------------------
# Sarvam Voice Agents: on-start hook, API tools, end-of-call webhook (see voice_sarvam.py)
# ---------------------------------------------------------------------------
async def _sarvam_tenant(authorization: str | None, k: str | None = None) -> str:
    from app.voice_sarvam import read_hook_token

    if k and (tid := read_hook_token(k)):
        return tid
    return await voice_tenant((authorization or "").removeprefix("Bearer ").strip() or None)


@router.post("/v1/voice/sarvam/start")
async def sarvam_start(request: Request, authorization: str | None = Header(None), k: str | None = None):
    from app import voice_sarvam

    return jsonable(await voice_sarvam.on_start(await _sarvam_tenant(authorization, k), await request.json()))


@router.post("/v1/voice/sarvam/tools/{name}")
async def sarvam_tool(name: str, request: Request, authorization: str | None = Header(None), k: str | None = None):
    from app import voice_sarvam

    try:
        body = await request.json()
    except Exception:
        body = {}
    return jsonable(await voice_sarvam.tool(await _sarvam_tenant(authorization, k), name, body if isinstance(body, dict) else {}))


@router.post("/v1/voice/sarvam/webhook")
@router.post("/v1/voice/sarvam/end")
async def sarvam_end(request: Request, authorization: str | None = Header(None), k: str | None = None):
    from app import voice_sarvam

    return jsonable(await voice_sarvam.on_end(await _sarvam_tenant(authorization, k), await request.json()))


# ---------------------------------------------------------------------------
# "Talk to us" inside the widget: a browser voice call (mic + speech) on the same brain, no telephony cost
# ---------------------------------------------------------------------------
class WebVoice(BaseModel):
    session: str
    text: str | None = None
    call_id: str | None = None


@router.post("/v1/widget/voice/start")
async def widget_voice_start(body: WebVoice, request: Request):
    from app import voice

    limit(request, "voice", 6)
    sess = _read_session(body.session)
    async with tenant_session(sess["t"]) as db:
        prof = await load_profile(db)
        if not prof["voice"].get("enabled", True):
            raise HTTPException(403, "Voice is turned off for this business")
        if blocked := await _caps_ok(db, sess["t"], sess["v"], prof["widget"]):
            raise HTTPException(429, blocked)
        phone = await db.val("SELECT c.primary_phone FROM customer_identities i JOIN customers c ON c.id=i.customer_id "
                             "WHERE i.kind='webchat_visitor' AND i.value=:v", v=sess["v"]) if sess.get("vf") else None
    started = await voice.start_call(sess["t"], direction="inbound", from_number=phone, to_number=None, purpose="web_call")
    async with tenant_session(sess["t"]) as db:
        await db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id", x={"visitor": sess["v"], "channel": "widget"}, id=started["call_id"])
    return {"call_id": started["call_id"], "greeting": started["greeting"], "lang": "hi-IN" if prof["voice"].get("language") in ("hi", "hinglish") else "en-IN"}


async def _own_call(sess: dict, call_id: str | None) -> str:
    async with tenant_session(sess["t"]) as db:
        ok = await db.val("SELECT 1 FROM voice_calls WHERE id=:id AND context->>'visitor'=:v", id=call_id, v=sess["v"])
    if not ok:
        raise HTTPException(404, "call not found")
    return call_id


@router.post("/v1/widget/voice/turn")
async def widget_voice_turn(body: WebVoice, request: Request):
    from app import voice

    limit(request, "voice-turn", 30)
    sess = _read_session(body.session)
    return jsonable(await voice.turn(sess["t"], await _own_call(sess, body.call_id), (body.text or "")[:600]))


@router.post("/v1/widget/voice/end")
async def widget_voice_end(body: WebVoice):
    from app import voice

    sess = _read_session(body.session)
    return jsonable(await voice.end_call(sess["t"], await _own_call(sess, body.call_id)))

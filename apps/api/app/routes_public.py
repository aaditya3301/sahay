"""Public endpoints: auth, widget, channel webhooks, media."""

import secrets

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from app.channels import integration, verify_meta_signature, wa_download_media
from app.config import settings
from app.core import check_password, get_media, hash_password, make_token, norm_email, read_token, sniff_mime
from app.db import system_session, tenant_session
from app.pipeline import InboundAttachment, InboundMessage, ingest

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class Login(BaseModel):
    email: str
    password: str


class Signup(BaseModel):
    name: str
    email: str
    password: str
    workspace: str
    website_url: str | None = None
    business_description: str = ""


async def _tenants_for(db, uid: str) -> list[dict]:
    return await db.all(
        "SELECT t.id, t.slug, t.name, m.role FROM tenant_members m JOIN tenants t ON t.id=m.tenant_id WHERE m.user_id=:u ORDER BY t.name",
        u=uid,
    )


@router.post("/v1/auth/login")
async def login(body: Login):
    async with system_session() as db:
        u = await db.one("SELECT * FROM users WHERE email=:e", e=body.email.lower().strip())
        if not u or not check_password(body.password, u["password_hash"]):
            raise HTTPException(401, "Wrong email or password")
        return {"token": make_token(str(u["id"])), "user": {"id": u["id"], "name": u["name"], "email": u["email"]},
                "tenants": await _tenants_for(db, str(u["id"]))}


async def create_tenant(name: str, slug: str | None = None, **settings_kw) -> dict:
    import re

    slug = slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    async with system_session() as db:
        if await db.one("SELECT 1 FROM tenants WHERE slug=:s", s=slug):
            slug = f"{slug}-{secrets.token_hex(2)}"
        t = await db.one("INSERT INTO tenants (slug, name) VALUES (:s, :n) RETURNING *", s=slug, n=name)
        key = f"pk_{slug.replace('-', '')}_{secrets.token_hex(6)}"
        await db.exec("INSERT INTO channel_routes (channel, external_key, tenant_id) VALUES ('webchat', :k, :t)", k=key, t=t["id"])
        if settings.inbound_email_domain:
            await db.exec("INSERT INTO channel_routes (channel, external_key, tenant_id) VALUES ('email', :k, :t) ON CONFLICT DO NOTHING",
                          k=f"{slug}@{settings.inbound_email_domain}", t=t["id"])
    settings_kw.setdefault("ticket_prefix", "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)[:3]).upper() or "T")
    async with tenant_session(t["id"]) as db:
        cols = ["tenant_id"] + list(settings_kw)
        await db.exec(
            f"INSERT INTO tenant_settings ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
            tenant_id=t["id"], **settings_kw,
        )
        from app.policy import PolicyDoc

        await db.exec(
            "INSERT INTO policies (tenant_id, version, status, source_text, rules, confirmed_at) VALUES (:t, 1, 'active', :s, :r, now())",
            t=t["id"], s="(default) Every action needs approval until you write your rules.", r=PolicyDoc().model_dump(),
        )
    return t


@router.post("/v1/auth/signup")
async def signup(body: Signup):
    async with system_session() as db:
        if await db.one("SELECT 1 FROM users WHERE email=:e", e=body.email.lower()):
            raise HTTPException(409, "Email already registered")
        u = await db.one("INSERT INTO users (email, name, password_hash) VALUES (:e,:n,:p) RETURNING *",
                         e=body.email.lower().strip(), n=body.name, p=hash_password(body.password))
    t = await create_tenant(body.workspace, website_url=body.website_url, business_description=body.business_description)
    async with system_session() as db:
        await db.exec("INSERT INTO tenant_members (tenant_id, user_id, role) VALUES (:t,:u,'owner')", t=t["id"], u=u["id"])
        return {"token": make_token(str(u["id"])), "user": {"id": u["id"], "name": u["name"], "email": u["email"]},
                "tenants": await _tenants_for(db, str(u["id"]))}


# ---------------------------------------------------------------------------
# Media (attachments + product images)
# ---------------------------------------------------------------------------
@router.get("/media/{attachment_id}")
async def media(attachment_id: str, token: str = Query(...), tenant: str = Query(...)):
    read_token(token)
    async with tenant_session(tenant) as db:
        a = await db.one("SELECT uri, mime FROM attachments WHERE id=:id", id=attachment_id)
    if not a:
        raise HTTPException(404)
    return Response(await get_media(a["uri"]), media_type=a["mime"], headers={"Cache-Control": "private, max-age=3600"})


@router.get("/public/products/{tenant_slug}/{sku}.png")
async def product_image(tenant_slug: str, sku: str, damaged: bool = False):
    async with system_session() as db:
        t = await db.one("SELECT id FROM tenants WHERE slug=:s", s=tenant_slug)
    if not t:
        raise HTTPException(404)
    async with tenant_session(t["id"]) as db:
        p = await db.one("SELECT image_uri, attributes FROM commerce_products WHERE sku=:s", s=sku)
    uri = ((p or {}).get("attributes") or {}).get("damaged_image_uri") if damaged else (p or {}).get("image_uri")
    if not uri:
        raise HTTPException(404)
    data = await get_media(uri)
    return Response(data, media_type=sniff_mime(data, "image/jpeg"), headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# WhatsApp Cloud API webhook
# ---------------------------------------------------------------------------
@router.get("/webhooks/whatsapp")
async def wa_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == settings.whatsapp_verify_token and settings.whatsapp_verify_token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403)


@router.post("/webhooks/whatsapp")
async def wa_webhook(request: Request):
    body = await request.body()
    if not verify_meta_signature(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "bad signature")
    data = await request.json()
    for entry in data.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            pnid = (v.get("metadata") or {}).get("phone_number_id")
            async with system_session() as db:
                route = await db.one("SELECT tenant_id FROM channel_routes WHERE channel='whatsapp' AND external_key=:k", k=pnid)
            if not route:
                continue
            tid = str(route["tenant_id"])
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in v.get("contacts", [])}
            for st in v.get("statuses", []):
                async with tenant_session(tid) as db:
                    await db.exec("UPDATE messages SET status=:s WHERE channel='whatsapp' AND external_id=:e", s=st.get("status"), e=st.get("id"))
            for m in v.get("messages", []):
                await _wa_message(tid, m, names.get(m.get("from")))
    return {"ok": True}


async def _wa_message(tid: str, m: dict, profile_name: str | None):
    from app.approvals import decide, parse_token

    sender = "+" + m["from"].lstrip("+")
    typ = m.get("type")
    # Owner tapping an approval button
    if typ == "interactive" and (m.get("interactive") or {}).get("type") == "button_reply":
        bid = m["interactive"]["button_reply"]["id"]
        parsed = parse_token(bid)
        if parsed:
            async with tenant_session(tid) as db:
                owner = await db.val("SELECT owner_phone FROM tenant_settings")
                if owner and owner.replace(" ", "") == sender:
                    await decide(db, parsed[0], parsed[1], owner, "whatsapp")
            return
    async with tenant_session(tid) as db:
        cfg = await integration(db, "whatsapp")
    text, atts = "", []
    if typ == "text":
        text = m["text"]["body"]
    elif typ in ("image", "audio", "document", "video"):
        media = m[typ]
        text = media.get("caption", "")
        if cfg.get("access_token"):
            data, mime = await wa_download_media(cfg, media["id"])
            atts.append(InboundAttachment(data, mime))
    elif typ == "interactive":
        text = (m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}).get("title", "")
    elif typ == "location":
        loc = m["location"]
        text = f"[location] {loc.get('name', '')} {loc.get('address', '')} ({loc.get('latitude')},{loc.get('longitude')})"
    await ingest(tid, InboundMessage(channel="whatsapp", text=text, from_phone=sender, from_name=profile_name,
                                     external_id=m.get("id"), attachments=atts, verified=True))


# ---------------------------------------------------------------------------
# Email (Resend inbound webhook)
# ---------------------------------------------------------------------------
@router.post("/webhooks/email")
async def email_webhook(request: Request):
    import httpx

    payload = await request.json()
    data = payload.get("data", payload)
    tos = data.get("to") or []
    tos = [tos] if isinstance(tos, str) else tos
    async with system_session() as db:
        route = None
        for to in tos:
            route = await db.one("SELECT tenant_id FROM channel_routes WHERE channel='email' AND external_key=:k", k=norm_email(to))
            if route:
                break
    if not route:
        return {"ok": False, "reason": "no tenant for recipient"}
    text = data.get("text")
    if not text and data.get("email_id") and settings.resend_api_key:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"https://api.resend.com/emails/receiving/{data['email_id']}",
                            headers={"Authorization": f"Bearer {settings.resend_api_key}"})
            if r.status_code < 300:
                full = r.json()
                text = full.get("text") or full.get("html") or ""
    text = _strip_quoted(text or "")
    sender = data.get("from") or ""
    headers = data.get("headers") or {}
    msg_id = data.get("message_id") or headers.get("message-id") or data.get("email_id")
    thread = headers.get("in-reply-to") or headers.get("references") or msg_id
    await ingest(str(route["tenant_id"]), InboundMessage(
        channel="email", text=(f"Subject: {data.get('subject', '')}\n\n" + text).strip(), from_email=sender,
        from_name=sender.split("<")[0].strip().strip('"') or None, external_id=msg_id, thread_id=thread, subject=data.get("subject"),
    ))
    return {"ok": True}


def _strip_quoted(text: str) -> str:
    import re

    lines = []
    for line in text.splitlines():
        if re.match(r"^On .+wrote:$", line.strip()) or line.strip().startswith(">"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


@router.get("/public/sample-history.csv")
async def sample_history():
    from app.config import ROOT

    return Response((ROOT / "seed" / "sample-history.csv").read_bytes(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample-history.csv"})


# ---------------------------------------------------------------------------
# Scheduled jobs (called by Cloud Scheduler with a shared secret)
# ---------------------------------------------------------------------------
@router.post("/internal/cron/{job}")
async def cron(job: str, request: Request):
    import hmac as _hmac

    from app.core import enqueue

    if not settings.cron_secret or not _hmac.compare_digest(request.headers.get("x-cron-secret", ""), settings.cron_secret):
        raise HTTPException(403)
    async with system_session() as db:
        tenants = [str(r["id"]) for r in await db.all("SELECT id FROM tenants WHERE status='active'")]

    async def weekly(tid: str):
        from app import insights

        async with tenant_session(tid) as db:
            await insights.build_gap_suggestions(db, tid)
            await insights.weekly_report(db, tid)

    if job == "weekly-reports":
        for tid in tenants:
            enqueue(weekly, tid, name=f"weekly-{tid}")
        return {"queued": len(tenants)}
    if job == "tick":  # the single scheduler job: lifecycle every run, summary in the evening, weekly + newsletter when due
        from app.lifecycle import tick

        for tid in tenants:
            enqueue(tick, tid, name=f"tick-{tid}", queue="lifecycle")
        return {"queued": len(tenants)}
    if job == "lifecycle":
        from app.lifecycle import run_lifecycle

        for tid in tenants:
            enqueue(run_lifecycle, tid, name=f"lifecycle-{tid}", queue="lifecycle")
        return {"queued": len(tenants)}
    if job == "daily-summary":
        from app.lifecycle import daily_summary

        for tid in tenants:
            enqueue(daily_summary, tid, name=f"summary-{tid}", queue="lifecycle")
        return {"queued": len(tenants)}
    raise HTTPException(404, "unknown job")

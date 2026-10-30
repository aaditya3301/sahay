"""Dashboard API for sales and the Agent Profile: Agent Studio, leads, outreach, campaigns,
automations (lifecycle) and calls."""

import csv
import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app import lifecycle, sales, voice
from app.config import settings
from app.core import Ctx, CtxDep, audit, enqueue, jsonable, norm_phone
from app.db import system_session, tenant_session
from app.profile import SKILLS, custom_tool_specs, load_profile, new_secret_key, public_profile, register_secret

router = APIRouter(prefix="/v1")


# ---------------------------------------------------------------------------
# Agent Studio
# ---------------------------------------------------------------------------
@router.get("/studio")
async def studio(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
        s = await db.one("SELECT t.name, t.slug, s.brand_color, s.widget_greeting, s.brand_voice FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
    async with system_session() as sdb:
        key = await sdb.val("SELECT external_key FROM channel_routes WHERE channel='webchat' AND tenant_id=:t LIMIT 1", t=ctx.tenant_id)
    base = settings.public_base_url.rstrip("/")
    return jsonable({"profile": public_profile(prof), "site_key": key, "tenant": s, "public_base_url": base,
                     "loader_url": f"{base}/widget.js", "chat_url": f"{base}/w/{key}"})


class StudioBody(BaseModel):
    agent_name: str | None = None
    persona: str | None = None
    skills: dict | None = None
    custom_tools: list[dict] | None = None
    sales: dict | None = None
    voice: dict | None = None
    widget: dict | None = None


@router.put("/studio")
async def studio_save(body: StudioBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
        fields: dict = {}
        if body.agent_name is not None:
            fields["agent_name"] = body.agent_name.strip()[:40] or "Sahay"
        if body.persona is not None:
            fields["persona"] = body.persona[:2000]
        if body.skills is not None:
            fields["skills"] = {k: bool(v) for k, v in body.skills.items() if k in SKILLS}
        if body.custom_tools is not None:
            tools = []
            for t in body.custom_tools[:10]:
                if not t.get("name") or not str(t.get("url", "")).startswith("https://"):
                    raise HTTPException(400, f"Tool '{t.get('name') or '?'}' needs a name and an https:// URL")
                tools.append({k: t.get(k) for k in ("name", "description", "when_to_use", "url", "method", "parameters", "enabled")})
            fields["custom_tools"] = tools
        for k in ("sales", "voice", "widget"):
            v = getattr(body, k)
            if v is not None:
                fields[k] = {**prof[k], **v}
        if "widget" in fields:
            fields["widget"]["allowed_domains"] = [d.strip().lower() for d in fields["widget"].get("allowed_domains") or [] if d.strip()]
        if fields:
            await db.exec("UPDATE agent_profiles SET " + ", ".join(f"{k}=:{k}" for k in fields) + ", updated_at=now()", **fields)
            await audit(db, "human", ctx.user_name, "agent_profile.updated", "agent_profile", ctx.tenant_id, {"fields": list(fields)})
    if "voice" in fields:  # dialled numbers route calls to this business
        async with system_session() as sdb:
            await sdb.exec("DELETE FROM channel_routes WHERE channel='voice' AND tenant_id=:t", t=ctx.tenant_id)
            for n in fields["voice"].get("numbers") or []:
                if norm_phone(n):
                    await sdb.exec("INSERT INTO channel_routes (channel, external_key, tenant_id) VALUES ('voice',:k,:t) ON CONFLICT (channel, external_key) DO NOTHING",
                                   k=norm_phone(n), t=ctx.tenant_id)
    return {"ok": True}


@router.get("/studio/secret")
async def studio_secret(ctx: Ctx = CtxDep):
    if ctx.role != "owner":
        raise HTTPException(403, "Only the owner can see the secret key")
    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
        await audit(db, "human", ctx.user_name, "secret_key.revealed", "agent_profile", ctx.tenant_id)
    return {"secret_key": prof["secret_key"]}


@router.post("/studio/secret/rotate")
async def studio_rotate(ctx: Ctx = CtxDep):
    if ctx.role != "owner":
        raise HTTPException(403, "Only the owner can rotate the secret key")
    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
        slug = await db.val("SELECT slug FROM tenants WHERE id=:t", t=ctx.tenant_id)
        key = new_secret_key(slug)
        await db.exec("UPDATE agent_profiles SET secret_key=:k, updated_at=now()", k=key)
        await register_secret(db, key, old=prof["secret_key"])
        await audit(db, "human", ctx.user_name, "secret_key.rotated", "agent_profile", ctx.tenant_id)
    return {"secret_key": key}


class ToolTest(BaseModel):
    tool: dict
    args: dict = {}


@router.post("/studio/tools/test")
async def studio_tool_test(body: ToolTest, ctx: Ctx = CtxDep):
    from app.profile import call_custom_tool

    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
    spec = next(iter(custom_tool_specs({**prof, "custom_tools": [{**body.tool, "enabled": True}]}).values()), None)
    if not spec:
        raise HTTPException(400, "Tool needs a name")
    return jsonable(await call_custom_tool(prof, spec, body.args, {"name": "Test visitor"}))


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
@router.get("/leads")
async def leads(ctx: Ctx = CtxDep, group: str = "all", q: str = ""):
    stages = sales.OUTCOME_GROUPS.get(group)
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all(
            "SELECT l.*, (SELECT count(*) FROM outreach_emails e WHERE e.lead_id=l.id AND e.status='draft') AS drafts "
            "FROM leads l WHERE (CAST(:st AS text[]) IS NULL OR l.stage = ANY(:st)) AND (:q='' OR l.name ILIKE '%'||:q||'%' OR l.company ILIKE '%'||:q||'%' "
            "OR l.email ILIKE '%'||:q||'%' OR l.phone ILIKE '%'||:q||'%') ORDER BY l.score DESC NULLS LAST, l.created_at DESC LIMIT 300",
            st=stages, q=q)
        counts = {r["stage"]: r["n"] for r in await db.all("SELECT stage, count(*) AS n FROM leads GROUP BY stage")}
        searches = await db.all("SELECT * FROM lead_searches ORDER BY created_at DESC LIMIT 5")
        drafts = await db.val("SELECT count(*) FROM outreach_emails WHERE status='draft'")
    groups = {g: sum(counts.get(s, 0) for s in st) for g, st in sales.OUTCOME_GROUPS.items()}
    groups["all"] = sum(counts.values())
    return jsonable({"leads": rows, "groups": groups, "searches": searches, "drafts_pending": drafts})


@router.get("/leads/{lid}")
async def lead(lid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        l = await db.one("SELECT * FROM leads WHERE id=:id", id=lid)
        if not l:
            raise HTTPException(404)
        events = await db.all("SELECT * FROM lead_events WHERE lead_id=:id ORDER BY created_at DESC", id=lid)
        emails = await db.all("SELECT * FROM outreach_emails WHERE lead_id=:id ORDER BY created_at DESC", id=lid)
        calls = await db.all("SELECT id, direction, purpose, status, summary, outcome, created_at FROM voice_calls WHERE lead_id=:id "
                             "OR (CAST(:c AS uuid) IS NOT NULL AND customer_id=:c) ORDER BY created_at DESC", id=lid, c=l["customer_id"])
        msgs = []
        if l["customer_id"]:
            msgs = await db.all("SELECT m.direction, m.channel, m.text, m.author_type, m.created_at, t.number AS ticket, m.ticket_id FROM messages m "
                                "LEFT JOIN tickets t ON t.id=m.ticket_id WHERE m.customer_id=:c ORDER BY m.created_at DESC LIMIT 40", c=l["customer_id"])
    return jsonable({"lead": l, "events": events, "emails": emails, "calls": calls, "messages": msgs,
                     "reply_url": sales.reply_url(ctx.tenant_id, lid)})


class LeadPatch(BaseModel):
    stage: str | None = None
    notes: str | None = None
    next_action: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None


@router.patch("/leads/{lid}")
async def lead_patch(lid: str, body: LeadPatch, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        if body.stage:
            if body.stage not in sales.STAGES:
                raise HTTPException(400, "unknown stage")
            await sales.set_stage(db, lid, body.stage, ctx.user_name, "set by the team")
        fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "stage"}
        if "phone" in fields:
            fields["phone"] = norm_phone(fields["phone"])
        if fields:
            await db.exec("UPDATE leads SET " + ", ".join(f"{k}=:{k}" for k in fields) + ", updated_at=now() WHERE id=:id", id=lid, **fields)
            await sales.add_event(db, lid, "edited", f"{ctx.user_name} updated {', '.join(fields)}")
    return {"ok": True}


class LeadNew(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    website: str | None = None
    city: str | None = None
    interest: str | None = None
    notes: str | None = None


@router.post("/leads")
async def lead_new(body: LeadNew, ctx: Ctx = CtxDep):
    if not (body.email or body.phone or body.company):
        raise HTTPException(400, "Add at least a company, email or phone")
    async with tenant_session(ctx.tenant_id) as db:
        row, _ = await sales.upsert_lead(db, source="manual", **body.model_dump())
    enqueue(sales.qualify_lead, ctx.tenant_id, str(row["id"]), name="qualify", queue="sales")
    return jsonable(row)


class LeadSearch(BaseModel):
    query: str
    count: int = 8


@router.post("/leads/search")
async def lead_search(body: LeadSearch, ctx: Ctx = CtxDep):
    if len(body.query.strip()) < 4:
        raise HTTPException(400, "Describe who you're looking for")
    async with tenant_session(ctx.tenant_id) as db:
        s = await db.one("INSERT INTO lead_searches (tenant_id, query, target_count) VALUES (:t,:q,:n) RETURNING *",
                         t=ctx.tenant_id, q=body.query.strip(), n=max(1, min(body.count, 20)))
        await audit(db, "human", ctx.user_name, "leads.search", "lead_search", s["id"], {"query": body.query})
    enqueue(sales.find_leads, ctx.tenant_id, str(s["id"]), name="lead-search", queue="sales")
    return jsonable({"accepted": True, "search": s})


@router.get("/leads/searches/{sid}")
async def lead_search_status(sid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.one("SELECT * FROM lead_searches WHERE id=:id", id=sid))


@router.post("/leads/import")
async def lead_import(file: UploadFile = File(...), ctx: Ctx = CtxDep):
    text = (await file.read()).decode("utf-8-sig", errors="ignore")
    added = 0
    async with tenant_session(ctx.tenant_id) as db:
        for r in list(csv.DictReader(io.StringIO(text)))[:500]:
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            if not (r.get("email") or r.get("phone") or r.get("company")):
                continue
            row, created = await sales.upsert_lead(db, source="import", name=r.get("name"), email=r.get("email"), phone=r.get("phone"),
                                                   company=r.get("company"), website=r.get("website"), city=r.get("city"),
                                                   interest=r.get("interest") or r.get("notes"))
            if created:
                added += 1
                enqueue(sales.qualify_lead, ctx.tenant_id, str(row["id"]), name="qualify", queue="sales")
    return {"added": added}


@router.post("/leads/{lid}/qualify")
async def lead_qualify(lid: str, ctx: Ctx = CtxDep):
    enqueue(sales.qualify_lead, ctx.tenant_id, lid, name="qualify", queue="sales")
    return {"queued": True}


@router.post("/leads/{lid}/draft-email")
async def lead_draft(lid: str, ctx: Ctx = CtxDep, kind: str = "first"):
    async with tenant_session(ctx.tenant_id) as db:
        if not await db.val("SELECT email FROM leads WHERE id=:id", id=lid):
            raise HTTPException(400, "This lead has no email address")
    enqueue(sales.draft_first_email, ctx.tenant_id, lid, kind, name="draft-email", queue="sales")
    return {"queued": True}


@router.get("/outreach")
async def outreach(ctx: Ctx = CtxDep, status: str = "draft"):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all(
            "SELECT e.*, l.name, l.company, l.email AS to_email, l.score FROM outreach_emails e JOIN leads l ON l.id=e.lead_id "
            "WHERE e.status=:s ORDER BY e.created_at DESC LIMIT 100", s=status))


class EmailEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


@router.post("/outreach/{eid}/approve")
async def outreach_approve(eid: str, body: EmailEdit, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        if body.subject or body.body:
            await db.exec("UPDATE outreach_emails SET subject=coalesce(:s, subject), body=coalesce(:b, body) WHERE id=:id AND status='draft'",
                          s=body.subject, b=body.body, id=eid)
        return jsonable(await sales.send_outreach(db, eid, ctx.user_name))


@router.post("/outreach/{eid}/reject")
async def outreach_reject(eid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        lid = await db.val("UPDATE outreach_emails SET status='rejected' WHERE id=:id AND status='draft' RETURNING lead_id", id=eid)
        if lid:
            await sales.add_event(db, lid, "email_rejected", f"{ctx.user_name} rejected the email draft")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Campaigns + automations
# ---------------------------------------------------------------------------
@router.get("/campaigns")
async def campaigns(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 100")
    return jsonable({"campaigns": rows, "segments": [{"id": k, "label": v[0]} for k, v in lifecycle.SEGMENTS.items()] + [{"id": "leads", "label": "Open sales leads"}]})


class CampaignNew(BaseModel):
    name: str
    goal: str
    channel: str = "whatsapp"
    audience: dict = {"segment": "recent_buyers"}


@router.post("/campaigns")
async def campaign_new(body: CampaignNew, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        c = await db.one("INSERT INTO campaigns (tenant_id, name, goal, channel, audience) VALUES (:t,:n,:g,:ch,:a) RETURNING *",
                         t=ctx.tenant_id, n=body.name, g=body.goal, ch=body.channel if body.channel in ("whatsapp", "email") else "whatsapp", a=body.audience)
    await lifecycle.draft_campaign(ctx.tenant_id, str(c["id"]))
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.one("SELECT * FROM campaigns WHERE id=:id", id=c["id"]))


@router.get("/campaigns/{cid}")
async def campaign(cid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        c = await db.one("SELECT * FROM campaigns WHERE id=:id", id=cid)
        if not c:
            raise HTTPException(404)
        aud = await lifecycle.audience(db, c["audience"] or {}, limit=500) if c["status"] in ("draft", "approved") else None
        sends = await db.all("SELECT s.status, s.skip_reason, s.created_at, cu.display_name FROM campaign_sends s LEFT JOIN customers cu ON cu.id=s.customer_id "
                             "WHERE s.campaign_id=:id ORDER BY s.created_at LIMIT 300", id=cid)
    preview = None
    if aud:
        preview = {"eligible": len(aud["eligible"]), "suppressed": aud["suppressed"][:50],
                   "sample": [r["customer_name"] for r in aud["eligible"][:8]]}
    return jsonable({"campaign": c, "audience_preview": preview, "sends": sends})


class CampaignPatch(BaseModel):
    name: str | None = None
    message: str | None = None
    audience: dict | None = None


@router.patch("/campaigns/{cid}")
async def campaign_patch(cid: str, body: CampaignPatch, ctx: Ctx = CtxDep):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    async with tenant_session(ctx.tenant_id) as db:
        if fields:
            await db.exec("UPDATE campaigns SET " + ", ".join(f"{k}=:{k}" for k in fields) + " WHERE id=:id AND status='draft'", id=cid, **fields)
    return {"ok": True}


@router.post("/campaigns/{cid}/redraft")
async def campaign_redraft(cid: str, ctx: Ctx = CtxDep):
    await lifecycle.draft_campaign(ctx.tenant_id, cid)
    return {"ok": True}


@router.post("/campaigns/{cid}/approve")
async def campaign_approve(cid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        ok = await db.val("UPDATE campaigns SET status='approved', approved_by=:b WHERE id=:id AND status='draft' RETURNING id", b=ctx.user_name, id=cid)
        if not ok:
            raise HTTPException(400, "Only drafts can be approved")
        await audit(db, "human", ctx.user_name, "campaign.approved", "campaign", cid)
    enqueue(lifecycle.send_campaign, ctx.tenant_id, cid, name="campaign", queue="lifecycle")
    return {"ok": True}


@router.post("/campaigns/{cid}/reject")
async def campaign_reject(cid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE campaigns SET status='rejected' WHERE id=:id AND status='draft'", id=cid)
    return {"ok": True}


@router.get("/automations")
async def automations(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        events = await db.all("SELECT e.*, c.display_name FROM lifecycle_events e LEFT JOIN customers c ON c.id=e.customer_id "
                              "WHERE e.kind <> 'owner_summary' ORDER BY e.created_at DESC LIMIT 100")
        summary = await db.one("SELECT * FROM lifecycle_events WHERE kind='owner_summary' ORDER BY created_at DESC LIMIT 1")
        runs = await db.all("SELECT created_at, data FROM audit_log WHERE verb='lifecycle.run' ORDER BY created_at DESC LIMIT 5")
        prof = await load_profile(db)
    return jsonable({"events": events, "last_summary": summary, "runs": runs,
                     "settings": {"save_the_order": prof["sales"].get("save_the_order"), "cod_calls": prof["voice"].get("cod_calls", False),
                                  "follow_up_days": prof["sales"].get("follow_up_days"), "cart_reminders": prof["sales"].get("cart_reminders"),
                                  "cart_reminder_hours": prof["sales"].get("cart_reminder_hours"), "newsletter": prof["sales"].get("newsletter")}})


@router.post("/automations/run")
async def automations_run(ctx: Ctx = CtxDep):
    return jsonable(await lifecycle.run_lifecycle(ctx.tenant_id))


@router.post("/automations/newsletter")
async def automations_newsletter(ctx: Ctx = CtxDep):
    c = await lifecycle.draft_newsletter(ctx.tenant_id, force=True)
    return jsonable(c)


@router.get("/automations/carts")
async def automations_carts(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all("SELECT id, cart_ref, customer_name, customer_phone, customer_email, items, total_paise, source, status, "
                                     "reminded_at, converted_at, updated_at FROM commerce_carts ORDER BY updated_at DESC LIMIT 100"))


@router.post("/automations/daily-summary")
async def automations_summary(ctx: Ctx = CtxDep):
    return {"text": await lifecycle.daily_summary(ctx.tenant_id)}


# ---------------------------------------------------------------------------
# Calls (voice) + browser test calls
# ---------------------------------------------------------------------------
@router.get("/calls")
async def calls(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all(
            "SELECT v.id, v.direction, v.purpose, v.from_number, v.to_number, v.status, v.summary, v.outcome, v.next_step, v.scheduled_for, "
            "v.started_at, v.ended_at, v.created_at, c.display_name, t.number AS ticket_number, jsonb_array_length(v.transcript) AS turns "
            "FROM voice_calls v LEFT JOIN customers c ON c.id=v.customer_id LEFT JOIN tickets t ON t.id=v.ticket_id ORDER BY v.created_at DESC LIMIT 200"))


@router.get("/calls/{call_id}")
async def call_detail(call_id: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        c = await db.one("SELECT v.*, cu.display_name, t.number AS ticket_number FROM voice_calls v LEFT JOIN customers cu ON cu.id=v.customer_id "
                         "LEFT JOIN tickets t ON t.id=v.ticket_id WHERE v.id=:id", id=call_id)
    if not c:
        raise HTTPException(404)
    return jsonable(c)


class TestCall(BaseModel):
    phone: str | None = None
    purpose: str = "inbound"
    call_id: str | None = None


@router.post("/voice/test/start")
async def test_call_start(body: TestCall, ctx: Ctx = CtxDep):
    direction = "outbound" if body.call_id or body.purpose != "inbound" else "inbound"
    return jsonable(await voice.start_call(ctx.tenant_id, direction=direction, from_number=body.phone if direction == "inbound" else None,
                                           to_number=body.phone if direction == "outbound" else None, purpose=body.purpose,
                                           call_id=body.call_id, test=True))


class TestTurn(BaseModel):
    text: str


@router.post("/voice/test/{call_id}/turn")
async def test_call_turn(call_id: str, body: TestTurn, ctx: Ctx = CtxDep):
    return jsonable(await voice.turn(ctx.tenant_id, call_id, body.text))


@router.post("/voice/test/{call_id}/end")
async def test_call_end(call_id: str, ctx: Ctx = CtxDep):
    return jsonable(await voice.end_call(ctx.tenant_id, call_id))


# ---------------------------------------------------------------------------
# Customer journey (lead + calls) for the customer page
# ---------------------------------------------------------------------------
@router.get("/customers/{cid}/journey")
async def journey(cid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        lead = await db.one("SELECT * FROM leads WHERE customer_id=:c ORDER BY created_at DESC LIMIT 1", c=cid)
        events = await db.all("SELECT * FROM lead_events WHERE lead_id=:l ORDER BY created_at DESC LIMIT 50", l=lead["id"]) if lead else []
        calls_ = await db.all("SELECT id, direction, purpose, status, summary, outcome, created_at FROM voice_calls WHERE customer_id=:c ORDER BY created_at DESC", c=cid)
        proactive = await db.all("SELECT kind, summary, data, created_at FROM lifecycle_events WHERE customer_id=:c ORDER BY created_at DESC LIMIT 20", c=cid)
        campaigns_ = await db.all("SELECT s.status, s.created_at, c.name FROM campaign_sends s JOIN campaigns c ON c.id=s.campaign_id WHERE s.customer_id=:c "
                                  "ORDER BY s.created_at DESC LIMIT 20", c=cid)
    stage = "customer" if (lead and lead["stage"] == "customer") else (lead["stage"] if lead else None)
    return jsonable({"lead": lead, "lead_events": events, "calls": calls_, "proactive": proactive, "campaigns": campaigns_, "stage": stage})


# ---------------------------------------------------------------------------
# Voice provider (Sarvam) setup + outbound dialing
# ---------------------------------------------------------------------------
@router.get("/voice/sarvam")
async def sarvam_setup(ctx: Ctx = CtxDep):
    from app import voice_sarvam
    from app.channels import integration
    from app.tools import TOOL_SPECS
    from app.voice import _voice_tools

    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
        cfg = await integration(db, "sarvam")
    specs = {**TOOL_SPECS, **custom_tool_specs(prof)}
    tools = [{"name": n, "description": specs[n]["description"], "parameters": specs[n]["parameters"]} for n in _voice_tools(prof) if n in specs]
    sk = prof["secret_key"] or ""
    return jsonable({**voice_sarvam.template(ctx.tenant_id, tools, f"{sk[:8]}...(your secret key)"),
                     "connected": {k: bool(cfg.get(k)) for k in ("api_key", "org_id", "workspace_id", "app_id", "connection_id", "agent_phone_number")},
                     "config": {k: cfg.get(k) for k in ("org_id", "workspace_id", "app_id", "app_version", "connection_id", "agent_phone_number")}})


class DialBody(BaseModel):
    phone: str
    purpose: str = "check_in"


@router.post("/voice/sarvam/test-call")
async def sarvam_test_call(body: DialBody, ctx: Ctx = CtxDep):
    from app import voice_sarvam

    call_id = await voice_sarvam.queue_test_call(ctx.tenant_id, body.phone, body.purpose)
    try:
        return jsonable(await voice_sarvam.place_call(ctx.tenant_id, call_id))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.post("/calls/{call_id}/dial")
async def dial(call_id: str, ctx: Ctx = CtxDep):
    from app import voice_livekit, voice_sarvam

    async with tenant_session(ctx.tenant_id) as db:
        provider = (await load_profile(db))["voice"].get("provider")
    try:
        if provider == "livekit":
            return jsonable(await voice_livekit.place_call(ctx.tenant_id, call_id))
        return jsonable(await voice_sarvam.place_call(ctx.tenant_id, call_id))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# LiveKit + Sarvam (our own voice agent): status, browser test calls on the real voice, outbound test calls
# ---------------------------------------------------------------------------
@router.get("/voice/livekit")
async def livekit_status(ctx: Ctx = CtxDep):
    from app import voice_livekit
    from app.channels import integration

    async with tenant_session(ctx.tenant_id) as db:
        sarvam = await integration(db, "sarvam")
        lkcfg = await integration(db, "livekit")
        prof = await load_profile(db)
    return {"configured": voice_livekit.configured(), "sarvam_key": bool(settings.sarvam_api_key or sarvam.get("api_key")),
            "sip_trunk": bool(lkcfg.get("sip_trunk_id") or settings.livekit_sip_trunk_id), "agent_name": settings.livekit_agent_name,
            "numbers": prof["voice"].get("numbers") or [], "voice": voice_livekit.voice_config(prof, sarvam)["tts"]}


@router.post("/voice/livekit/web-call")
async def livekit_web_call(body: TestCall, ctx: Ctx = CtxDep):
    from app import voice_livekit

    try:
        return jsonable(await voice_livekit.web_call(ctx.tenant_id, phone=body.phone, purpose=body.purpose,
                                                     identity=f"dash-{ctx.user_id}", name=ctx.user_name or "Dashboard"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/voice/livekit/test-call")
async def livekit_test_call(body: DialBody, ctx: Ctx = CtxDep):
    from app import voice_livekit

    try:
        return jsonable(await voice_livekit.queue_and_dial(ctx.tenant_id, body.phone, body.purpose))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Developers: outgoing webhooks, incoming events, Integration Health
# ---------------------------------------------------------------------------
class WebhookBody(BaseModel):
    url: str
    events: list[str] = ["*"]


@router.get("/webhooks")
async def webhooks(ctx: Ctx = CtxDep):
    from app.hooks_out import EVENTS

    async with tenant_session(ctx.tenant_id) as db:
        eps = await db.all("SELECT id, url, events, active, created_at, left(secret, 10) || '...' AS secret_hint FROM webhook_endpoints ORDER BY created_at")
        dels = await db.all("SELECT d.id, d.event, d.status, d.attempts, d.last_status_code, d.last_error, d.created_at, e.url FROM webhook_deliveries d "
                            "JOIN webhook_endpoints e ON e.id=d.endpoint_id ORDER BY d.created_at DESC LIMIT 50")
    return jsonable({"endpoints": eps, "deliveries": dels, "events": EVENTS})


@router.post("/webhooks")
async def webhook_add(body: WebhookBody, ctx: Ctx = CtxDep):
    import secrets as _s

    from app.hooks_out import _allowed

    if not _allowed(body.url):
        raise HTTPException(400, "Use a public https:// URL")
    secret = f"whsec_{_s.token_hex(16)}"
    async with tenant_session(ctx.tenant_id) as db:
        row = await db.one("INSERT INTO webhook_endpoints (tenant_id, url, events, secret) VALUES (:t,:u,:e,:s) RETURNING id",
                           t=ctx.tenant_id, u=body.url, e=body.events or ["*"], s=secret)
        await audit(db, "human", ctx.user_name, "webhook.added", "webhook", row["id"], {"url": body.url})
    return {"id": str(row["id"]), "secret": secret}


@router.delete("/webhooks/{wid}")
async def webhook_delete(wid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("DELETE FROM webhook_endpoints WHERE id=:id", id=wid)
    return {"ok": True}


@router.post("/webhooks/{wid}/test")
async def webhook_test(wid: str, ctx: Ctx = CtxDep):
    from app.core import new_id
    from app.hooks_out import deliver

    async with tenant_session(ctx.tenant_id) as db:
        d = await db.one("INSERT INTO webhook_deliveries (tenant_id, endpoint_id, event, payload) VALUES (:t,:e,'webhook.test',:p) RETURNING id",
                         t=ctx.tenant_id, e=wid, p={"id": new_id(), "type": "webhook.test", "data": {"hello": "from Sahay"}})
    await deliver(ctx.tenant_id, str(d["id"]))
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.one("SELECT status, attempts, last_status_code, last_error FROM webhook_deliveries WHERE id=:id", id=d["id"]))


@router.get("/integration-events")
async def integration_events(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all("SELECT external_id, type, status, result, created_at FROM integration_events ORDER BY created_at DESC LIMIT 50"))


@router.get("/integrations/health")
async def integration_health(ctx: Ctx = CtxDep):
    """Green / amber / red for each way the business is connected to us."""
    import time as _t

    from app.channels import email_ready, integration
    from app.commerce import CustomCommerce, get_commerce

    checks = []

    def add(key, name, status, detail):
        checks.append({"key": key, "name": name, "status": status, "detail": detail})

    async with tenant_session(ctx.tenant_id) as db:
        prof = await load_profile(db)
        w = await db.one("SELECT count(*) AS n FROM messages WHERE channel='webchat' AND direction='in' AND created_at > now()-interval '7 days'")
        add("widget", "Website widget", "green" if w["n"] else "amber",
            (f"{w['n']} visitor messages in 7 days" if w["n"] else "No visitor messages yet: paste the snippet on your site")
            + ("" if prof["widget"].get("allowed_domains") else " · no allowed domains set (works on any site)"))
        shop = await get_commerce(db)
        t0 = _t.perf_counter()
        try:
            status = "green"
            if isinstance(shop, CustomCommerce) and shop.url:
                await shop.call("ping", {})
                detail = "your endpoint answered a signed ping"
            elif isinstance(shop, CustomCommerce):
                n = await db.val("SELECT count(*) FROM commerce_orders")
                detail, status = f"no endpoint yet: using {n} orders your events sent (level 2)", "amber"
            else:
                await shop.search_products("a", 1)
                detail = {"mock": "demo store (sandbox)", "shopify": "Shopify connected", "woocommerce": "WooCommerce connected"}.get(shop.name, shop.name)
            add("store", "Store (Commerce Contract)", status, f"{detail} · {int((_t.perf_counter() - t0) * 1000)} ms")
        except Exception as e:
            add("store", "Store (Commerce Contract)", "red", f"{shop.name}: {str(e)[:140]}")
        ev = await db.one("SELECT count(*) FILTER (WHERE created_at > now()-interval '7 days') AS n, "
                          "count(*) FILTER (WHERE status='failed' AND created_at > now()-interval '7 days') AS failed FROM integration_events")
        add("events", "Events from your site", "red" if ev["failed"] else ("green" if ev["n"] else "amber"),
            (f"{ev['n']} events in 7 days" + (f", {ev['failed']} failed" if ev["failed"] else "")) if ev["n"] else "No events yet (optional, level 2)")
        wh = await db.one("SELECT (SELECT count(*) FROM webhook_endpoints WHERE active) AS eps, "
                          "count(*) FILTER (WHERE status='delivered') AS ok, count(*) FILTER (WHERE status='failed') AS bad "
                          "FROM webhook_deliveries WHERE created_at > now()-interval '7 days'")
        add("webhooks", "Webhooks to you", "amber" if not wh["eps"] else ("red" if wh["bad"] and not wh["ok"] else "green"),
            f"{wh['eps']} endpoint(s) · {wh['ok']} delivered, {wh['bad']} failed (7 days)" if wh["eps"] else "None set (optional)")
        wa, em, sv = await integration(db, "whatsapp"), await integration(db, "email"), await integration(db, "sarvam")
        add("whatsapp", "WhatsApp", "green" if wa.get("access_token") else "amber", "connected" if wa.get("access_token") else "simulated until you add Meta credentials")
        add("email", "Email", "green" if email_ready(em) else "amber",
            "connected (Brevo)" if email_ready(em) else "simulated until you add a Brevo key and a verified sender")
        last_call = await db.val("SELECT max(created_at) FROM voice_calls")
        sarvam_ok = all(sv.get(k) for k in ("api_key", "org_id", "workspace_id", "app_id", "connection_id", "agent_phone_number"))
        add("voice", "Voice agent (Sarvam)", "green" if sarvam_ok else "amber",
            ("Sarvam connected" if sarvam_ok else "Sarvam not connected yet: browser calls and the voice API still work")
            + (f" · last call {str(last_call)[:16]}" if last_call else ""))
        run = await db.val("SELECT max(created_at) FROM audit_log WHERE verb='lifecycle.run'")
        add("scheduler", "Automations", "green" if run else "amber", f"last run {str(run)[:16]}" if run else "not run yet")
    return jsonable({"checks": checks})

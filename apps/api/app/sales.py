"""Sales: leads from every door (widget, Reply Now chat, WhatsApp, voice, outbound search, CSV),
email checks, scoring, the first email with a signed Reply Now link, and outcome groups.

Outbound pattern borrowed from the sales repo: save the request → answer "accepted" → a worker on the
`sales` queue does the slow part → the dashboard shows the result.
"""

import json
import logging
import re
from datetime import timedelta
from typing import Literal

import httpx
import jwt
from google.genai import types
from pydantic import BaseModel, Field

from app import llm
from app.config import settings
from app.core import audit, enqueue, norm_email, norm_phone, now
from app.db import tenant_session

log = logging.getLogger("sahay.sales")

STAGES = ["new", "contacted", "engaged", "qualified", "callback", "customer", "dropped", "needs_attention"]
OUTCOME_GROUPS = {
    "new": ["new"], "in_progress": ["contacted", "engaged", "qualified"], "callback": ["callback"],
    "customer": ["customer"], "dropped": ["dropped"], "needs_attention": ["needs_attention"],
}


# ---------------------------------------------------------------------------
# Leads + timeline
# ---------------------------------------------------------------------------
async def add_event(db, lead_id, kind: str, summary: str, data: dict | None = None) -> None:
    await db.exec("INSERT INTO lead_events (tenant_id, lead_id, kind, summary, data) VALUES (:t,:l,:k,:s,:d)",
                  t=db.tenant_id, l=lead_id, k=kind, s=summary, d=data or {})


async def upsert_lead(db, *, source: str, name: str | None = None, email: str | None = None, phone: str | None = None,
                      company: str | None = None, website: str | None = None, city: str | None = None,
                      interest: str | None = None, notes: str | None = None, customer_id=None, search_id=None,
                      stage: str | None = None, enrichment: dict | None = None) -> tuple[dict, bool]:
    email, phone = norm_email(email), norm_phone(phone)
    existing = None
    if customer_id:
        existing = await db.one("SELECT * FROM leads WHERE customer_id=:c ORDER BY created_at DESC LIMIT 1", c=customer_id)
    if not existing and (email or phone):
        existing = await db.one(
            "SELECT * FROM leads WHERE (CAST(:e AS text) IS NOT NULL AND email=:e) OR (CAST(:p AS text) IS NOT NULL AND phone=:p) LIMIT 1",
            e=email, p=phone)
    if not existing and company and not (email or phone):
        existing = await db.one("SELECT * FROM leads WHERE lower(company)=lower(:c) LIMIT 1", c=company)
    if existing:
        row = await db.one(
            "UPDATE leads SET name=coalesce(name,:n), email=coalesce(email,:e), phone=coalesce(phone,:p), company=coalesce(company,:co), "
            "website=coalesce(website,:w), city=coalesce(city,:ci), interest=coalesce(:i, interest), notes=coalesce(:no, notes), "
            "customer_id=coalesce(customer_id,:cu), stage=coalesce(:st, stage), enrichment=enrichment || :en, updated_at=now() WHERE id=:id RETURNING *",
            n=name, e=email, p=phone, co=company, w=website, ci=city, i=interest, no=notes, cu=customer_id, st=stage,
            en=enrichment or {}, id=existing["id"])
        return row, False
    row = await db.one(
        "INSERT INTO leads (tenant_id, customer_id, search_id, name, company, email, phone, website, city, source, stage, interest, notes, enrichment) "
        "VALUES (:t,:cu,:s,:n,:co,:e,:p,:w,:ci,:src,:st,:i,:no,:en) RETURNING *",
        t=db.tenant_id, cu=customer_id, s=search_id, n=name, co=company, e=email, p=phone, w=website, ci=city, src=source,
        st=stage or "new", i=interest, no=notes, en=enrichment or {})
    await add_event(db, row["id"], "created", f"Lead created from {source}", {"interest": interest})
    from app.hooks_out import emit

    await emit(db, "lead.created", {"id": str(row["id"]), "name": name, "company": company, "email": email, "phone": phone,
                                    "source": source, "interest": interest})
    return row, True


async def fetch_lead(tenant_id: str, lead_id: str) -> dict | None:
    """Jobs can start before the request that queued them has committed: wait briefly for the row."""
    import asyncio

    for _ in range(10):
        async with tenant_session(tenant_id) as db:
            lead = await db.one("SELECT * FROM leads WHERE id=:id", id=lead_id)
        if lead:
            return lead
        await asyncio.sleep(1)
    log.warning("lead %s not found", lead_id)
    return None


async def set_stage(db, lead_id, stage: str, by: str, reason: str = "") -> None:
    cur = await db.val("SELECT stage FROM leads WHERE id=:id", id=lead_id)
    if cur == stage:
        return
    await db.exec("UPDATE leads SET stage=:s, updated_at=now() WHERE id=:id", s=stage, id=lead_id)
    await add_event(db, lead_id, "stage", f"{cur} → {stage}" + (f" · {reason}" if reason else ""), {"by": by})


async def lead_for_customer(db, customer_id) -> dict | None:
    return await db.one("SELECT * FROM leads WHERE customer_id=:c ORDER BY created_at DESC LIMIT 1", c=customer_id)


async def after_chat_turn(db, customer: dict, intent: str | None, channel: str) -> None:
    """Keep the lead timeline in sync with conversations: replies move a contacted lead to engaged,
    and an angry support ticket flags the lead for attention."""
    lead = await lead_for_customer(db, customer["id"])
    if not lead:
        return
    await db.exec("UPDATE leads SET last_contact_at=now(), updated_at=now() WHERE id=:id", id=lead["id"])
    if lead["stage"] in ("new", "contacted"):
        await set_stage(db, lead["id"], "engaged", "ai", f"replied on {channel}")
    if (customer.get("frustration_score") or 0) >= 60 and lead["stage"] not in ("customer", "dropped"):
        await set_stage(db, lead["id"], "needs_attention", "ai", "frustrated in a conversation")


# ---------------------------------------------------------------------------
# Tools the agent can call (sales + voice)
# ---------------------------------------------------------------------------
async def run_tool(ctx, name: str, args: dict) -> dict:
    db = ctx.db
    channel = (ctx.conversation or {}).get("channel") or "voice"
    if name == "capture_lead":
        if not (args.get("phone") or args.get("email") or ctx.customer.get("primary_phone") or ctx.customer.get("primary_email")):
            return {"ok": False, "note": "Ask for a phone number or email first, then call capture_lead again."}
        lead, created = await upsert_lead(
            db, source=channel, name=args.get("name") or ctx.customer.get("display_name"),
            email=args.get("email") or ctx.customer.get("primary_email"), phone=args.get("phone") or ctx.customer.get("primary_phone"),
            company=args.get("company"), interest=args.get("interest"), notes=args.get("notes"), customer_id=ctx.customer["id"],
        )
        if args.get("name") and not ctx.customer.get("display_name"):
            await db.exec("UPDATE customers SET display_name=:n WHERE id=:c", n=args["name"], c=ctx.customer["id"])
        ctx.lead_id = str(lead["id"])
        ctx.ui["lead_captured"] = True
        await add_event(db, lead["id"], "chat", f"AI captured interest on {channel}: {args.get('interest')}", {"ticket": str(ctx.ticket["id"])})
        enqueue(score_lead, db.tenant_id, str(lead["id"]), name="score-lead", queue="sales")
        return {"ok": True, "lead_id": str(lead["id"]), "new": created,
                "note": "Saved. Tell them the team has their details; keep helping with their question."}
    if name == "book_callback":
        phone = args.get("phone") or ctx.customer.get("primary_phone")
        if not phone:
            return {"ok": False, "note": "Ask for the best phone number to call back on."}
        lead, _ = await upsert_lead(db, source=channel, name=args.get("name") or ctx.customer.get("display_name"), phone=phone,
                                    interest=args.get("topic"), customer_id=ctx.customer["id"], stage="callback")
        when = args.get("preferred_time") or "as soon as possible"
        await db.exec("UPDATE leads SET next_action=:a, next_action_at=now() + interval '2 hours', stage='callback', updated_at=now() WHERE id=:id",
                      a=f"Call back ({when}): {args.get('topic')}", id=lead["id"])
        await add_event(db, lead["id"], "callback_booked", f"Callback requested for {when}: {args.get('topic')}")
        await db.exec(
            "INSERT INTO voice_calls (tenant_id, direction, purpose, to_number, customer_id, lead_id, status, context, scheduled_for) "
            "VALUES (:t,'outbound','callback',:p,:c,:l,'queued',:x, now() + interval '2 hours')",
            t=db.tenant_id, p=norm_phone(phone), c=ctx.customer["id"], l=lead["id"], x={"topic": args.get("topic"), "preferred_time": when})
        ctx.lead_id = str(lead["id"])
        ctx.ui["lead_captured"] = True
        return {"ok": True, "note": f"Callback booked ({when}). Confirm the time and number back to them."}
    if name == "send_link_whatsapp":
        text, removed = vet_links(args.get("text", ""), ctx)
        if not text.strip():
            return {"ok": False, "note": "Nothing verified to send. Share the courier and AWB from the order instead of a link."}
        args = {**args, "text": text}
        ctx.ui.setdefault("whatsapp_sent", []).append(text)
        phone = ctx.customer.get("primary_phone")
        if not phone:
            return {"ok": False, "note": "No phone number on file; ask where to send it."}
        from app.channels import integration, wa_send

        cfg = await integration(db, "whatsapp")
        status = "simulated"
        if cfg.get("access_token") and cfg.get("phone_number_id"):
            try:
                await wa_send(cfg, phone, {"type": "text", "text": {"body": args.get("text", "")[:4096]}})
                status = "sent"
            except Exception:
                status = "failed"
        await audit(db, "ai", "Sahay AI", "voice.whatsapp_link", "customer", ctx.customer["id"], {"text": args.get("text"), "status": status})
        note = "Sent on WhatsApp. Tell them to check WhatsApp."
        if removed:
            note += f" (Removed {len(removed)} link(s) that weren't from real data; never invent URLs.)"
        return {"ok": status != "failed", "status": status, "note": note}
    if name == "transfer_to_human":
        ctx.ui["transfer"] = args.get("summary", "")
        ctx.escalation = {"reason": "caller transferred", "summary": args.get("summary", "")}
        prof = ctx.profile or {}
        number = (prof.get("voice") or {}).get("transfer_number")
        return {"ok": True, "transfer_to": number or None,
                "note": "Say you're connecting them to a teammate now." if number else "No transfer number set: say a teammate will call back shortly."}
    return {"error": "not implemented"}


URL_RX = re.compile(r"https?://[^\s)>\]]+")


def vet_links(text: str, ctx) -> tuple[str, list[str]]:
    """Keep only links the agent actually saw (tool results, knowledge base, the owner's booking link)."""
    prof = ctx.profile or {}
    known = " ".join(ctx.seen) + json.dumps(ctx.kb_hits, default=str) + " " + str((prof.get("sales") or {}).get("booking_url") or "")
    removed = [u for u in URL_RX.findall(text) if u.rstrip(".,!") not in known]
    for u in removed:
        text = text.replace(u, "")
    return re.sub(r"[ 	]{2,}", " ", text).strip(), removed


# ---------------------------------------------------------------------------
# Email checks (no SMTP probing: Cloud Run blocks port 25). Syntax + MX + disposable + role.
# ---------------------------------------------------------------------------
DISPOSABLE = {"mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com", "yopmail.com", "trashmail.com", "sharklasers.com"}
ROLE = {"info", "sales", "contact", "hello", "support", "admin", "office", "enquiry", "enquiries", "team"}


async def verify_email(email: str | None) -> tuple[str, str]:
    e = norm_email(email)
    if not e:
        return "invalid", "not an email address"
    local, domain = e.split("@", 1)
    if domain in DISPOSABLE:
        return "invalid", "disposable domain"
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get("https://dns.google/resolve", params={"name": domain, "type": "MX"})
            answers = r.json().get("Answer") or []
    except Exception:
        return "unknown", "DNS lookup failed"
    if not answers:
        return "invalid", "domain has no mail server"
    if local in ROLE:
        return "risky", "shared inbox (role address)"
    return "valid", "mail server found"


async def email_on_site(email: str, website: str | None) -> bool:
    if not website:
        return False
    root = website if website.startswith("http") else f"https://{website}"
    root = root.rstrip("/")
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Sahay lead check)"}) as c:
        for path in ("", "/contact", "/contact-us", "/pages/contact", "/about"):
            try:
                r = await c.get(root + path)
                if r.status_code < 400 and email.lower() in r.text.lower():
                    return True
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# Lead finding: Gemini with Google Search grounding, then structured extraction
# ---------------------------------------------------------------------------
class FoundLead(BaseModel):
    company: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    city: str | None = None
    why_fit: str = ""


class FoundLeads(BaseModel):
    leads: list[FoundLead] = []


async def find_leads(tenant_id: str, search_id: str) -> None:
    async with tenant_session(tenant_id) as db:
        s = await db.one("SELECT * FROM lead_searches WHERE id=:id", id=search_id)
        srow = await db.one("SELECT t.name, s.business_description FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        from app.profile import load_profile

        prof = await load_profile(db)
    icp = prof["sales"].get("icp") or "businesses that would buy in bulk or stock our products"
    prompt = (
        f"We are {srow['name']}: {srow['business_description']}\nIdeal customer: {icp}\n\n"
        f"Find {s['target_count']} real businesses matching: {s['query']}.\n"
        "For each: company name, city, website, a public business email or phone if listed on their site or directory, "
        "a contact person if public, and one line on why they fit. Only include businesses you found in search results."
    )
    try:
        resp = await llm.generate(model=settings.model_resolver, contents=prompt, temperature=0.2, purpose="lead_search",
                                  tenant_id=tenant_id, tools=[types.Tool(google_search=types.GoogleSearch())])
        found_text = resp.text or ""
        grounded = True
    except Exception as e:  # grounding unavailable: fall back to model knowledge, flagged as unverified
        log.warning("grounded search failed: %s", e)
        found_text, grounded = "", False
    try:
        out = await llm.structured(
            FoundLeads, model=settings.model_resolver, purpose="lead_extract", tenant_id=tenant_id,
            system="Extract business leads as JSON. Never invent emails or phone numbers: leave them null unless they appear in the text.",
            contents=(f"Search results:\n{found_text}" if found_text else prompt + "\n(No search available: list well-known matching businesses; leave contact fields null.)"),
        )
    except Exception as e:
        async with tenant_session(tenant_id) as db:
            await db.exec("UPDATE lead_searches SET status='failed', error=:e WHERE id=:id", e=str(e)[:300], id=search_id)
        return
    added, new_ids = 0, []
    async with tenant_session(tenant_id) as db:
        for f in out.leads[: s["target_count"]]:
            lead, created = await upsert_lead(db, source="outbound", name=f.name, email=f.email, phone=f.phone, company=f.company,
                                              website=f.website, city=f.city, interest=f.why_fit, search_id=search_id,
                                              enrichment={"grounded": grounded, "query": s["query"]})
            if created:
                added += 1
                new_ids.append(str(lead["id"]))
        await db.exec("UPDATE lead_searches SET status='done', found=:n WHERE id=:id", n=added, id=search_id)
    for lid in new_ids:
        enqueue(qualify_lead, tenant_id, lid, name="qualify", queue="sales")


async def qualify_lead(tenant_id: str, lead_id: str) -> None:
    """verify → score → first email draft (owner approves before anything is sent)."""
    lead = await fetch_lead(tenant_id, lead_id)
    if not lead:
        return
    if lead["source"] == "outbound" and lead["email"] and not await email_on_site(lead["email"], lead["website"]):
        async with tenant_session(tenant_id) as db:  # search results can contain made-up addresses: never email those
            await db.exec("UPDATE leads SET email=NULL, email_status='not_found', enrichment=enrichment || :e WHERE id=:id",
                          e={"dropped_email": lead["email"]}, id=lead_id)
            await add_event(db, lead_id, "email_checked", f"Removed {lead['email']}: not found on their website")
            lead = await db.one("SELECT * FROM leads WHERE id=:id", id=lead_id)
    if lead["email"]:
        status, why = await verify_email(lead["email"])
        async with tenant_session(tenant_id) as db:
            await db.exec("UPDATE leads SET email_status=:s WHERE id=:id", s=status, id=lead_id)
            await add_event(db, lead_id, "email_checked", f"Email {status}: {why}")
    await score_lead(tenant_id, lead_id)
    async with tenant_session(tenant_id) as db:
        lead = await db.one("SELECT * FROM leads WHERE id=:id", id=lead_id)
    if lead["email"] and lead["email_status"] in ("valid", "risky", "unknown") and (lead["score"] or 0) >= 40:
        await draft_first_email(tenant_id, lead_id)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
class Score(BaseModel):
    score: int = Field(ge=0, le=100)
    reasons: list[str] = []
    suggested_stage: Literal["new", "qualified", "needs_attention", "dropped"] = "new"


async def score_lead(tenant_id: str, lead_id: str) -> None:
    if not await fetch_lead(tenant_id, lead_id):
        return
    async with tenant_session(tenant_id) as db:
        lead = await db.one("SELECT * FROM leads WHERE id=:id", id=lead_id)
        srow = await db.one("SELECT t.name, s.business_description FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        from app.profile import load_profile

        prof = await load_profile(db)
        chats = []
        if lead["customer_id"]:
            chats = await db.all("SELECT direction, text FROM messages WHERE customer_id=:c ORDER BY created_at DESC LIMIT 12", c=lead["customer_id"])
    info = {k: lead[k] for k in ("name", "company", "email", "email_status", "phone", "website", "city", "source", "interest", "notes")}
    convo = "\n".join(f"{'Visitor' if m['direction'] == 'in' else 'Agent'}: {m['text']}" for m in reversed(chats)) or "(no conversation)"
    try:
        sc = await llm.structured(
            Score, model=settings.model_triage, purpose="lead_score", tenant_id=tenant_id, thinking=settings.thinking_triage or None,
            system=("Score a sales lead 0-100 for how likely and how valuable a sale is. Reward: matches the ideal customer, clear "
                    "buying intent, bulk/repeat potential, reachable (verified email or phone). Penalise: no contact, invalid email, "
                    "unrelated business, just browsing. Give 2-4 short reasons."),
            contents=f"Business: {srow['name']} — {srow['business_description']}\nIdeal customer: {prof['sales'].get('icp') or '(not set)'}\n"
                     f"Lead: {json.dumps(info, default=str)}\nConversation:\n{convo}",
        )
    except Exception:
        log.exception("lead scoring failed")
        return
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE leads SET score=:s, score_reasons=:r, updated_at=now() WHERE id=:id", s=sc.score, r=sc.reasons, id=lead_id)
        await add_event(db, lead_id, "scored", f"Scored {sc.score}/100", {"reasons": sc.reasons})
        if lead["stage"] == "new" and sc.suggested_stage in ("qualified", "dropped"):
            await set_stage(db, lead_id, sc.suggested_stage, "ai", "score")


# ---------------------------------------------------------------------------
# First email + Reply Now link
# ---------------------------------------------------------------------------
def reply_token(tenant_id: str, lead_id: str, days: int = 30) -> str:
    return jwt.encode({"t": tenant_id, "l": lead_id, "typ": "reply", "exp": now() + timedelta(days=days)}, settings.jwt_secret, "HS256")


def read_reply_token(token: str) -> tuple[str, str] | None:
    try:
        d = jwt.decode(token, settings.jwt_secret, ["HS256"])
        return (d["t"], d["l"]) if d.get("typ") == "reply" else None
    except Exception:
        return None


def reply_url(tenant_id: str, lead_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/r/{reply_token(tenant_id, lead_id)}"


class EmailDraft(BaseModel):
    subject: str
    body: str


async def draft_first_email(tenant_id: str, lead_id: str, kind: str = "first") -> dict | None:
    if not await fetch_lead(tenant_id, lead_id):
        return None
    async with tenant_session(tenant_id) as db:
        lead = await db.one("SELECT * FROM leads WHERE id=:id", id=lead_id)
        srow = await db.one("SELECT t.name, s.business_description, s.brand_voice, s.website_url FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        from app.profile import load_profile

        prof = await load_profile(db)
        prev = await db.all("SELECT subject, body, sent_at FROM outreach_emails WHERE lead_id=:l AND status IN ('sent','simulated') ORDER BY created_at", l=lead_id)
    sender = prof["sales"].get("sender_name") or f"Team {srow['name']}"
    ask = ("Write a short, personal first email (under 120 words)." if kind == "first" else
           "Write a short, friendly follow-up (under 70 words) to the earlier email. No guilt-tripping.")
    try:
        d = await llm.structured(
            EmailDraft, model=settings.model_composer, purpose="outreach_email", tenant_id=tenant_id, temperature=0.6,
            system=(f"You write B2B sales emails for {srow['name']} ({srow['business_description']}). Voice: {srow['brand_voice']}. "
                    f"Offer: {prof['sales'].get('offer') or 'bulk and corporate orders'}. {ask} Mention one specific reason it fits them. "
                    "End with one soft question. Do not include any links or a signature; they are added after your text. Plain text."),
            contents=f"Lead: {json.dumps({k: lead[k] for k in ('name', 'company', 'city', 'website', 'interest')}, default=str)}\n"
                     f"Earlier emails: {json.dumps(prev, default=str) if prev else '(none)'}",
        )
    except Exception:
        log.exception("email draft failed")
        return None
    body = (f"{d.body.strip()}\n\nReply Now — chat with us instantly (no forms): {reply_url(tenant_id, lead_id)}\n\n"
            f"{sender}\n{srow['website_url'] or ''}").strip()
    async with tenant_session(tenant_id) as db:
        row = await db.one("INSERT INTO outreach_emails (tenant_id, lead_id, kind, subject, body) VALUES (:t,:l,:k,:s,:b) RETURNING *",
                           t=tenant_id, l=lead_id, k=kind, s=d.subject, b=body)
        await add_event(db, lead_id, "email_drafted", f"{'First' if kind == 'first' else 'Follow-up'} email drafted — waiting for your approval")
    return row


async def send_outreach(db, email_id: str, by: str) -> dict:
    """Owner approved the draft → send via Brevo if configured, otherwise record it as simulated."""
    from app.channels import EmailError, email_ready, email_send, inbound_address, integration, undeliverable

    em = await db.one("SELECT e.*, l.email AS to_email FROM outreach_emails e JOIN leads l ON l.id=e.lead_id WHERE e.id=:id", id=email_id)
    if em["status"] in ("sent", "simulated"):
        return em
    cfg = await integration(db, "email")
    status, ext, why = "simulated", None, ""
    if undeliverable(em["to_email"]):
        status, why = "skipped", "this address can't receive email (a demo or reserved domain)"
    elif email_ready(cfg) and em["to_email"]:
        from app.profile import load_profile

        brand = (await load_profile(db))["sales"].get("sender_name") or await db.val("SELECT name FROM tenants WHERE id=:t", t=db.tenant_id)
        try:
            ext = await email_send(cfg, em["to_email"], em["subject"], em["body"], sender=cfg.get("outreach_from") or None,
                                   reply_to=cfg.get("reply_to") or await inbound_address(db.tenant_id), tenant_id=db.tenant_id, brand=brand)
            status = "sent"
        except EmailError as e:
            status, why = "failed", str(e)
        except Exception as e:
            log.warning("outreach send failed: %s", e)
            status, why = "failed", "the email service didn't respond; try again"
    row = await db.one("UPDATE outreach_emails SET status=:s, external_id=:x, sent_at=CASE WHEN :s IN ('sent','simulated') THEN now() END "
                       "WHERE id=:id RETURNING *", s=status, x=ext, id=email_id)
    if status in ("sent", "simulated"):
        await db.exec("UPDATE leads SET last_contact_at=now(), stage=CASE WHEN stage IN ('new','qualified') THEN 'contacted' ELSE stage END, "
                      "next_action='Follow up if no reply', next_action_at=now() + make_interval(days => 3), updated_at=now() WHERE id=:l", l=em["lead_id"])
    await add_event(db, em["lead_id"], {"failed": "email_failed", "skipped": "email_skipped"}.get(status, "email_sent"),
                    f"Email '{em['subject']}' {status}" + (" (email channel not connected yet)" if status == "simulated" else f": {why}" if why else ""),
                    {"by": by, **({"reason": why} if why else {})})
    await audit(db, "human", by, "outreach.sent", "outreach_email", email_id, {"status": status})
    return row

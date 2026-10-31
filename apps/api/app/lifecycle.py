"""Lifecycle worker: everything on a schedule. Cloud Scheduler calls /internal/cron/lifecycle hourly and
/internal/cron/daily-summary every evening; the dashboard can also run them on demand.

- Proactive delay alerts: message customers whose parcel is late before they complain.
- COD confirmations: ask on Telegram (and queue a voice call when voice is on) before a COD order ships.
- Lead follow-ups: draft a follow-up for leads who didn't reply (owner approves), drop after the last one.
- Due callbacks: hand queued outbound calls to the voice pipeline.
- Abandoned carts: one gentle reminder a while after checkout was left (widget carts + Shopify abandoned checkouts).
- Owner's daily summary.
- Campaigns: draft → owner approves → send, never to someone with an open angry ticket or who opted out.
- Monthly newsletter: drafted from last month's data on the 1st, sent only after the owner approves.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
from pydantic import BaseModel

from app import llm
from app.channels import campaign_html, integration, send_to_customer, wa_send
from app.config import settings
from app.core import audit, enqueue, inr, norm_email, norm_phone
from app.db import tenant_session

log = logging.getLogger("sahay.lifecycle")
MAX_PER_RUN = 15


async def _claim(db, kind: str, ref: str, customer_id=None, summary: str = "", data: dict | None = None) -> bool:
    """Record a proactive event once (idempotent across runs)."""
    row = await db.one(
        "INSERT INTO lifecycle_events (tenant_id, kind, ref, customer_id, summary, data) VALUES (:t,:k,:r,:c,:s,:d) "
        "ON CONFLICT (tenant_id, kind, ref) DO NOTHING RETURNING id",
        t=db.tenant_id, k=kind, r=ref, c=customer_id, s=summary, d=data or {})
    return bool(row)


async def _customer_conv(db, name: str | None, phone: str | None, email: str | None, channel: str) -> tuple[dict, dict]:
    from app.pipeline import InboundMessage, resolve_customer

    cust = await resolve_customer(db, InboundMessage(channel=channel, from_phone=phone, from_email=email, from_name=name, verified=True))
    thread = None
    if channel in ("whatsapp", "telegram"):  # phone-based customers reach us on Telegram once they have started the bot
        thread = await db.val("SELECT value FROM customer_identities WHERE customer_id=:c AND kind='telegram' LIMIT 1", c=cust["id"])
        if thread:
            channel = "telegram"
    conv = await db.one("SELECT * FROM conversations WHERE customer_id=:c AND channel=:ch ORDER BY created_at DESC LIMIT 1", c=cust["id"], ch=channel)
    if not conv:
        conv = await db.one("INSERT INTO conversations (tenant_id, customer_id, channel, external_thread_id) VALUES (:t,:c,:ch,:th) RETURNING *",
                            t=db.tenant_id, c=cust["id"], ch=channel, th=thread)
    return cust, conv


async def _angry(db, customer_id) -> str | None:
    row = await db.one(
        "SELECT number, frustration_score, sensitive FROM tickets WHERE customer_id=:c AND status NOT IN ('resolved','closed') "
        "AND (frustration_score >= 50 OR sentiment <= -0.4 OR sensitive) ORDER BY updated_at DESC LIMIT 1", c=customer_id)
    return f"open ticket #{row['number']} (frustration {int(row['frustration_score'])})" if row else None


class Short(BaseModel):
    text: str


async def _write(tenant_id: str, srow: dict, instruction: str, facts: dict) -> str:
    out = await llm.structured(
        Short, model=settings.model_composer, purpose="proactive", tenant_id=tenant_id, thinking=settings.thinking_composer or None,
        system=f"You write short Telegram messages for {srow['name']}. Voice: {srow['brand_voice']}. Hinglish if the brand uses it. "
               "Max 3 lines. Only use the facts given. No links.",
        contents=f"{instruction}\nFacts: {json.dumps(facts, default=str)}")
    return out.text.strip()


async def _sarvam_ready(tenant_id: str) -> bool:
    async with tenant_session(tenant_id) as db:
        cfg = await integration(db, "sarvam")
    return all(cfg.get(k) for k in ("api_key", "org_id", "workspace_id", "app_id", "connection_id", "agent_phone_number"))


# ---------------------------------------------------------------------------
async def run_lifecycle(tenant_id: str) -> dict:
    from app.profile import load_profile

    if settings.automations_paused:
        return {"paused": True}
    done = {"delay_alerts": 0, "cod_confirmations": 0, "cart_reminders": 0, "followups_drafted": 0, "leads_dropped": 0, "calls_dispatched": 0}
    async with tenant_session(tenant_id) as db:
        prof = await load_profile(db)
        srow = await db.one("SELECT t.name, s.brand_voice FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        late = await db.all(
            "SELECT * FROM commerce_orders WHERE status IN ('in_transit','shipped') AND expected_delivery IS NOT NULL AND expected_delivery < CURRENT_DATE "
            "ORDER BY expected_delivery LIMIT :n", n=MAX_PER_RUN)
        cod = await db.all(
            "SELECT * FROM commerce_orders WHERE payment_method='cod' AND status='processing' AND NOT ('cod_confirmed' = ANY(tags)) "
            "AND placed_at > now() - interval '3 days' ORDER BY placed_at DESC LIMIT :n", n=MAX_PER_RUN)

    # 1. delay alerts
    for o in late:
        async with tenant_session(tenant_id) as db:
            if not await _claim(db, "delay_alert", o["order_ref"], summary=f"Delay alert for #{o['order_ref']}"):
                continue
            cust, conv = await _customer_conv(db, o["customer_name"], o["customer_phone"], o["customer_email"], "telegram")
            days = (date.today() - o["expected_delivery"]).days
            text = await _write(tenant_id, srow, "Tell the customer, before they ask, that their parcel is running late, say sorry once, "
                                "and that we're tracking it with the courier and will update them. Offer help by replying here.",
                                {"name": (o["customer_name"] or "").split(" ")[0], "order": o["order_ref"], "courier": o["courier"],
                                 "days_late": days, "items": [li["title"] for li in o["line_items"]]})
            msg = await send_to_customer(db, conv, text, None, "ai", "Proactive alert")
            await db.exec("UPDATE lifecycle_events SET customer_id=:c, data=:d WHERE kind='delay_alert' AND ref=:r",
                          c=cust["id"], d={"text": text, "status": msg["status"], "days_late": days}, r=o["order_ref"])
            done["delay_alerts"] += 1

    # 2. COD confirmations (Telegram now; a voice call is queued when voice is on)
    for o in cod:
        async with tenant_session(tenant_id) as db:
            if not await _claim(db, "cod_confirm", o["order_ref"], summary=f"COD confirmation for #{o['order_ref']} ({inr(o['total_paise'])})"):
                continue
            cust, conv = await _customer_conv(db, o["customer_name"], o["customer_phone"], o["customer_email"], "telegram")
            text = (f"Namaste {(o['customer_name'] or '').split(' ')[0]} ji 🙏 {srow['name']} se. Aapka COD order #{o['order_ref']} "
                    f"({inr(o['total_paise'])}) ship hone wala hai. Confirm karne ke liye 'Confirm' reply karein, ya 'Cancel' agar nahi chahiye.")
            msg = await send_to_customer(db, conv, text, None, "ai", "COD confirmation",
                                         interactive={"buttons": [{"id": f"cod_yes:{o['order_ref']}", "title": "Confirm"},
                                                                  {"id": f"cod_no:{o['order_ref']}", "title": "Cancel"}]})
            if prof["voice"].get("enabled") and prof["voice"].get("cod_calls"):
                await db.exec("INSERT INTO voice_calls (tenant_id, direction, purpose, to_number, customer_id, status, context, scheduled_for) "
                              "VALUES (:t,'outbound','cod_confirmation',:p,:c,'queued',:x, now() + interval '4 hours')",
                              t=tenant_id, p=norm_phone(o["customer_phone"]), c=cust["id"], x={"order_ref": o["order_ref"]})
            await db.exec("UPDATE lifecycle_events SET customer_id=:c, data=:d WHERE kind='cod_confirm' AND ref=:r",
                          c=cust["id"], d={"text": text, "status": msg["status"]}, r=o["order_ref"])
            done["cod_confirmations"] += 1

    # 3. abandoned carts
    if prof["sales"].get("cart_reminders", True):
        done["cart_reminders"] = await remind_carts(tenant_id, srow, float(prof["sales"]["cart_reminder_hours"] if prof["sales"].get("cart_reminder_hours") is not None else 1))

    # 4. lead follow-ups
    from app.sales import draft_first_email, set_stage

    async with tenant_session(tenant_id) as db:
        due = await db.all(
            "SELECT l.id, (SELECT count(*) FROM outreach_emails e WHERE e.lead_id=l.id AND e.status IN ('sent','simulated')) AS sent, "
            "(SELECT count(*) FROM outreach_emails e WHERE e.lead_id=l.id AND e.status='draft') AS drafts "
            "FROM leads l WHERE l.stage='contacted' AND l.next_action_at <= now() LIMIT :n", n=MAX_PER_RUN)
    max_emails = 1 + int(prof["sales"].get("max_followups", 2))
    for d in due:
        if d["drafts"]:
            continue
        if d["sent"] >= max_emails:
            async with tenant_session(tenant_id) as db:
                await set_stage(db, d["id"], "dropped", "ai", f"no reply after {d['sent']} emails")
                await db.exec("UPDATE leads SET next_action=NULL, next_action_at=NULL WHERE id=:id", id=d["id"])
            done["leads_dropped"] += 1
        elif await draft_first_email(tenant_id, str(d["id"]), kind="followup"):
            async with tenant_session(tenant_id) as db:
                await db.exec("UPDATE leads SET next_action='Approve follow-up email', next_action_at=NULL WHERE id=:id", id=d["id"])
            done["followups_drafted"] += 1

    # 5. due outbound calls → our LiveKit agent, Sarvam Voice Agents, or the Naaradh webhook
    hook = prof["voice"].get("webhook_url")
    provider = prof["voice"].get("provider", "sarvam")
    if prof["voice"].get("enabled") and ((provider == "sarvam" and await _sarvam_ready(tenant_id)) or provider == "livekit"):
        if provider == "livekit":
            from app.voice_livekit import place_call
        else:
            from app.voice_sarvam import place_call

        async with tenant_session(tenant_id) as db:
            due = await db.all("SELECT id FROM voice_calls WHERE status='queued' AND direction='outbound' AND scheduled_for <= now() LIMIT 10")
        for c in due:
            try:
                await place_call(tenant_id, str(c["id"]))
                done["calls_dispatched"] += 1
            except Exception as e:
                log.warning("%s dial failed: %s", provider, e)
    elif hook and prof["voice"].get("enabled"):
        async with tenant_session(tenant_id) as db:
            calls = await db.all("SELECT * FROM voice_calls WHERE status='queued' AND direction='outbound' AND scheduled_for <= now() LIMIT 10")
        for c in calls:
            try:
                async with httpx.AsyncClient(timeout=10) as h:
                    r = await h.post(hook, json={"call_id": str(c["id"]), "to": c["to_number"], "purpose": c["purpose"], "context": c["context"],
                                                 "start_url": f"{settings.public_base_url}/v1/voice/calls/start"})
                    r.raise_for_status()
                async with tenant_session(tenant_id) as db:
                    await db.exec("UPDATE voice_calls SET status='ringing' WHERE id=:id", id=c["id"])
                done["calls_dispatched"] += 1
            except Exception as e:
                log.warning("voice dispatch failed: %s", e)
    async with tenant_session(tenant_id) as db:
        await audit(db, "system", "Lifecycle worker", "lifecycle.run", "tenant", tenant_id, done)
    return done


# ---------------------------------------------------------------------------
async def daily_summary(tenant_id: str) -> str:
    async with tenant_session(tenant_id) as db:
        s = await db.one(
            "SELECT (SELECT count(*) FROM tickets WHERE created_at > now()-interval '24 hours' AND NOT is_simulation) AS tickets, "
            "(SELECT count(*) FROM tickets WHERE resolved_at > now()-interval '24 hours' AND resolution_type='ai_auto' AND NOT is_simulation) AS ai_resolved, "
            "(SELECT count(*) FROM approvals WHERE status='pending') AS approvals, "
            "(SELECT count(*) FROM tickets WHERE status='escalated') AS escalated, "
            "(SELECT count(*) FROM leads WHERE created_at > now()-interval '24 hours') AS new_leads, "
            "(SELECT count(*) FROM leads WHERE score >= 70 AND stage NOT IN ('customer','dropped')) AS hot_leads, "
            "(SELECT count(*) FROM outreach_emails WHERE status='draft') AS email_drafts, "
            "(SELECT count(*) FROM voice_calls WHERE created_at > now()-interval '24 hours') AS calls, "
            "(SELECT count(*) FROM customers WHERE frustration_score >= 60 AND NOT is_simulation) AS at_risk")
        srow = await db.one("SELECT t.name, s.owner_phone FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        top = await db.one("SELECT complaint_summary FROM tickets WHERE created_at > now()-interval '24 hours' AND complaint_summary IS NOT NULL "
                           "GROUP BY complaint_summary ORDER BY count(*) DESC LIMIT 1")
    lines = [f"📊 {srow['name']} · today",
             f"• {s['tickets']} new conversations, {s['ai_resolved']} solved by AI",
             f"• {s['approvals']} waiting for your approval, {s['escalated']} with the team",
             f"• {s['new_leads']} new leads · {s['hot_leads']} hot · {s['email_drafts']} emails to approve",
             f"• {s['calls']} calls · {s['at_risk']} unhappy customers to watch"]
    if top:
        lines.append(f"• Top complaint: {top['complaint_summary']}")
    text = "\n".join(lines)
    async with tenant_session(tenant_id) as db:
        cfg = await integration(db, "whatsapp")
        status = "simulated"
        if cfg.get("access_token") and srow["owner_phone"]:
            try:
                await wa_send(cfg, norm_phone(srow["owner_phone"]), {"type": "text", "text": {"body": text}})
                status = "sent"
            except Exception:
                status = "failed"
        await db.exec("INSERT INTO lifecycle_events (tenant_id, kind, ref, summary, data) VALUES (:t,'owner_summary',:r,:s,:d) "
                      "ON CONFLICT (tenant_id, kind, ref) DO UPDATE SET summary=:s, data=:d, created_at=now()",
                      t=tenant_id, r=str(date.today()), s="Owner's daily summary", d={"text": text, "status": status, "stats": s})
    return text


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
# Campaigns only ever reach converted customers (people with an order). Sales leads who haven't bought are never in an
# audience: they're contacted one by one from Leads.
SEGMENTS = {
    "all": ("All customers (everyone who ordered)", "SELECT DISTINCT ON (customer_phone) customer_name, customer_phone, customer_email FROM commerce_orders "
                                                   "WHERE status NOT IN ('cancelled','payment_pending')"),
    "recent_buyers": ("Bought in the last 30 days", "SELECT DISTINCT ON (customer_phone) customer_name, customer_phone, customer_email FROM commerce_orders WHERE status='delivered' AND placed_at > now()-interval '30 days'"),
    "repeat": ("Ordered 2+ times", "SELECT customer_name, customer_phone, max(customer_email) AS customer_email FROM commerce_orders GROUP BY customer_name, customer_phone HAVING count(*) >= 2"),
    "inactive": ("No order in 45+ days", "SELECT customer_name, customer_phone, max(customer_email) AS customer_email FROM commerce_orders GROUP BY customer_name, customer_phone HAVING max(placed_at) < now()-interval '45 days'"),
}


async def audience(db, aud: dict, limit: int = 2000) -> dict:
    seg = aud.get("segment") or "all"
    rows = await db.all(SEGMENTS.get(seg, SEGMENTS["all"])[1] + " LIMIT :n", n=limit)
    eligible, suppressed = [], []
    for r in rows:
        cust = await db.one("SELECT c.id, c.marketing_opt_out FROM customer_identities i JOIN customers c ON c.id=i.customer_id "
                            "WHERE i.kind='phone' AND i.value=:p LIMIT 1", p=norm_phone(r["customer_phone"])) if r["customer_phone"] else None
        if not cust and r.get("customer_email"):
            cust = await db.one("SELECT c.id, c.marketing_opt_out FROM customer_identities i JOIN customers c ON c.id=i.customer_id "
                                "WHERE i.kind='email' AND i.value=:e LIMIT 1", e=norm_email(r["customer_email"]))
        if aud.get("channel") == "email" and not r.get("customer_email"):
            continue
        reason = ("opted out of marketing" if cust["marketing_opt_out"] else await _angry(db, cust["id"])) if cust else None
        if reason:
            suppressed.append({"name": r["customer_name"], "reason": reason})
        else:
            eligible.append(r)
    return {"eligible": eligible, "suppressed": suppressed}


class CampaignDraft(BaseModel):
    subject: str = ""
    message: str


# How an email must be laid out: the sender turns blank lines into paragraphs and "- " lines into a bulleted list
EMAIL_LAYOUT = ("Plain text, no markdown, no links, and laid out like a real email: 'Hi {first_name},' on its own line; then 2 or 3 short "
                "paragraphs of 1 to 3 sentences each, with a blank line between paragraphs; if you mention products, put each on its own "
                "line starting with '- ' (name and real price); one clear call to action as its own short paragraph; finish with "
                "'Warmly,' on one line and the business name on the next. Use {first_name} literally as a placeholder.")


async def draft_campaign(tenant_id: str, campaign_id: str) -> None:
    async with tenant_session(tenant_id) as db:
        c = await db.one("SELECT * FROM campaigns WHERE id=:id", id=campaign_id)
        srow = await db.one("SELECT t.name, s.brand_voice, s.business_description FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        products = await db.all("SELECT title, price_paise, stock FROM commerce_products WHERE stock > 0 ORDER BY title LIMIT 12")
    if c["channel"] == "email":
        system = (f"Write one marketing email for {srow['name']} ({srow['business_description']}). Voice: {srow['brand_voice']}. "
                  "Give a subject line (under 55 characters, no emoji, not in capitals) and the message. Under 130 words. " + EMAIL_LAYOUT +
                  " Only mention products from the list with their real prices. No fake urgency, no made-up discounts unless the goal states one.")
    else:
        system = (f"Write one {c['channel']} marketing message for {srow['name']} ({srow['business_description']}). Voice: {srow['brand_voice']}. "
                  "Max 5 short lines, one clear call to action, start with 'Hi {first_name}'. Use {first_name} literally as a placeholder. "
                  "Only mention products from the list with their real prices. No fake urgency, no made-up discounts unless the goal states one.")
    d = await llm.structured(
        CampaignDraft, model=settings.model_composer, purpose="campaign_draft", tenant_id=tenant_id, temperature=0.7, system=system,
        contents=f"Goal: {c['goal']}\nAudience: {(c['audience'] or {}).get('segment')}\nProducts in stock: "
                 + json.dumps([{"title": p["title"], "price": inr(p["price_paise"])} for p in products], ensure_ascii=False))
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE campaigns SET message=:m, subject=coalesce(nullif(:s, ''), subject) WHERE id=:id", m=d.message, s=d.subject.strip(), id=campaign_id)


async def send_campaign(tenant_id: str, campaign_id: str) -> None:
    async with tenant_session(tenant_id) as db:
        c = await db.one("SELECT * FROM campaigns WHERE id=:id", id=campaign_id)
        aud = await audience(db, {**(c["audience"] or {}), "channel": c["channel"]})
        await db.exec("UPDATE campaigns SET status='sending' WHERE id=:id", id=campaign_id)
        store = await db.one("SELECT t.name, s.website_url FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
    site = (store["website_url"] or "").strip()
    store_url = (site if re.match(r"^https?://", site, re.I) else f"https://{site}") if site and "." in site else None  # the Shop now button
    stats = {"sent": 0, "simulated": 0, "failed": 0, "suppressed": len(aud["suppressed"])}
    for r in aud["eligible"][:500]:
        async with tenant_session(tenant_id) as db:
            cust, conv = await _customer_conv(db, r["customer_name"], r["customer_phone"], r["customer_email"],
                                              "email" if c["channel"] == "email" else "telegram")
            text = c["message"].replace("{first_name}", (r["customer_name"] or "there").split(" ")[0])
            page = None
            if c["channel"] == "email":  # a formatted email: business name on top, paragraphs, optional Shop now button (no unsubscribe link)
                page = campaign_html(store["name"], text, store_url)
            else:
                text += "\n\nReply STOP to stop offers."
            msg = await send_to_customer(db, conv, text, None, "ai", f"Campaign: {c['name']}", subject=c.get("subject") or c["name"], html=page)
            st = msg["status"] if msg["status"] in ("sent", "simulated", "skipped") else "failed"
            stats[st] = stats.get(st, 0) + 1
            await db.exec("INSERT INTO campaign_sends (tenant_id, campaign_id, customer_id, status) VALUES (:t,:c,:cu,:s)",
                          t=tenant_id, c=campaign_id, cu=cust["id"], s=st)
    async with tenant_session(tenant_id) as db:
        for s in aud["suppressed"]:
            await db.exec("INSERT INTO campaign_sends (tenant_id, campaign_id, status, skip_reason) VALUES (:t,:c,'skipped',:r)",
                          t=tenant_id, c=campaign_id, r=f"{s['name']}: {s['reason']}")
        await db.exec("UPDATE campaigns SET status='sent', sent_at=now(), stats=:s WHERE id=:id", s=stats, id=campaign_id)
        await audit(db, "system", "Lifecycle worker", "campaign.sent", "campaign", campaign_id, stats)


# ---------------------------------------------------------------------------
# Abandoned carts
# ---------------------------------------------------------------------------
async def sync_shopify_carts(db) -> None:
    from app.commerce import get_commerce

    shop = await get_commerce(db)
    if not hasattr(shop, "abandoned_checkouts"):
        return
    try:
        rows = await shop.abandoned_checkouts()
    except Exception as e:
        log.warning("shopify abandoned checkouts failed: %s", e)
        return
    for c in rows:
        await db.exec(
            "INSERT INTO commerce_carts (tenant_id, cart_ref, customer_name, customer_phone, customer_email, items, total_paise, checkout_url, source, created_at, updated_at) "
            "VALUES (:t,:r,:n,:p,:e,:i,:tot,:u,'shopify',:c,:up) ON CONFLICT (tenant_id, cart_ref) DO UPDATE SET items=:i, total_paise=:tot, updated_at=:up",
            t=db.tenant_id, r=c["cart_ref"], n=c["customer_name"], p=norm_phone(c["customer_phone"]), e=norm_email(c["customer_email"]),
            i=c["items"], tot=c["total_paise"], u=c["checkout_url"], c=c["created_at"], up=c["updated_at"])


async def remind_carts(tenant_id: str, srow: dict, after_hours: float) -> int:
    sent = 0
    async with tenant_session(tenant_id) as db:
        await sync_shopify_carts(db)
        await db.exec("UPDATE commerce_carts SET status='expired' WHERE status='open' AND updated_at < now() - interval '72 hours'")
        carts = await db.all(
            "SELECT * FROM commerce_carts WHERE status='open' AND updated_at < now() - make_interval(mins => :m) "
            "AND (customer_phone IS NOT NULL OR customer_email IS NOT NULL) AND jsonb_array_length(items) > 0 ORDER BY updated_at LIMIT :n",
            m=int(after_hours * 60), n=MAX_PER_RUN)
    for cart in carts:
        async with tenant_session(tenant_id) as db:
            bought = await db.one(
                "SELECT order_ref FROM commerce_orders WHERE ((CAST(:p AS text) IS NOT NULL AND customer_phone=:p) OR "
                "(CAST(:e AS text) IS NOT NULL AND lower(customer_email)=:e)) AND placed_at > :c LIMIT 1",
                p=cart["customer_phone"], e=cart["customer_email"], c=cart["created_at"])
            if bought:
                await db.exec("UPDATE commerce_carts SET status='converted', converted_at=now() WHERE id=:id", id=cart["id"])
                continue
            if not await _claim(db, "cart_reminder", str(cart["id"]), summary=f"Cart reminder · {inr(cart['total_paise'])}"):
                continue
            channel = "telegram" if cart["customer_phone"] else "email"
            cust, conv = await _customer_conv(db, cart["customer_name"], cart["customer_phone"], cart["customer_email"], channel)
            skip = "opted out" if cust.get("marketing_opt_out") else await _angry(db, cust["id"])
            if skip:
                await db.exec("UPDATE commerce_carts SET status='reminded', reminded_at=now() WHERE id=:id", id=cart["id"])
                await db.exec("UPDATE lifecycle_events SET customer_id=:c, data=:d WHERE kind='cart_reminder' AND ref=:r",
                              c=cust["id"], d={"skipped": skip}, r=str(cart["id"]))
                continue
            text = await _write(tenant_id, srow,
                                "Gently remind the customer they left items in their cart. Name the items and total, offer help with "
                                "any doubt (size, delivery time, COD) and say they can reply here to order. One reminder only, no pressure, no fake discounts.",
                                {"name": (cart["customer_name"] or "").split(" ")[0], "items": [f"{i['qty']} x {i['title']}" for i in cart["items"]],
                                 "total": inr(cart["total_paise"])})
            if cart["checkout_url"]:
                text += f"\n\nComplete your order: {cart['checkout_url']}"
            msg = await send_to_customer(db, conv, text, None, "ai", "Cart reminder", subject="You left something in your cart")
            await db.exec("UPDATE commerce_carts SET status='reminded', reminded_at=now() WHERE id=:id", id=cart["id"])
            await db.exec("UPDATE lifecycle_events SET customer_id=:c, data=:d WHERE kind='cart_reminder' AND ref=:r",
                          c=cust["id"], d={"text": text, "status": msg["status"], "items": cart["items"]}, r=str(cart["id"]))
            lead = await db.one("SELECT id FROM leads WHERE customer_id=:c LIMIT 1", c=cust["id"])
            if lead:
                from app.sales import add_event

                await add_event(db, lead["id"], "cart_reminder", f"Cart reminder sent ({inr(cart['total_paise'])})")
            sent += 1
    return sent


# ---------------------------------------------------------------------------
# Monthly newsletter + unsubscribe
# ---------------------------------------------------------------------------
def unsubscribe_url(tenant_id: str, customer_id: str) -> str:
    import jwt

    tok = jwt.encode({"typ": "unsub", "t": tenant_id, "c": customer_id}, settings.jwt_secret, "HS256")
    return f"{settings.public_base_url.rstrip('/')}/u/{tok}"


class Newsletter(BaseModel):
    subject: str
    body: str


async def draft_newsletter(tenant_id: str, force: bool = False) -> dict | None:
    month = date.today().strftime("%Y-%m")
    async with tenant_session(tenant_id) as db:
        if not force and await db.one("SELECT 1 FROM lifecycle_events WHERE kind='newsletter' AND ref=:r", r=month):
            return None
        srow = await db.one("SELECT t.name, s.brand_voice, s.business_description, s.website_url FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        best = await db.all(
            "SELECT li->>'title' AS title, sum((li->>'qty')::int) AS qty FROM commerce_orders, jsonb_array_elements(line_items) li "
            "WHERE placed_at > now() - interval '30 days' AND status <> 'cancelled' GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
        products = await db.all("SELECT title, price_paise, description FROM commerce_products WHERE stock > 0 ORDER BY title LIMIT 12")
        faqs = await db.all("SELECT intent, count(*) AS n FROM tickets WHERE created_at > now() - interval '30 days' AND NOT is_simulation "
                            "AND intent IN ('product_question','pre_sales','delivery_delay','cod_query','return_request') GROUP BY intent ORDER BY n DESC LIMIT 3")
    n = await llm.structured(
        Newsletter, model=settings.model_composer, purpose="newsletter", tenant_id=tenant_id, temperature=0.7,
        system=f"Write the monthly email newsletter for {srow['name']} ({srow['business_description']}). Voice: {srow['brand_voice']}. "
               "120-200 words: a warm hello with {first_name}, this month's bestsellers (one per line, starting with '- '), one useful tip "
               "that answers a common customer question, and one soft call to action. Give a short, friendly subject (under 55 characters, "
               "no emoji). " + EMAIL_LAYOUT + " Use only the products and prices given. No made-up offers.",
        contents=json.dumps({"month": date.today().strftime("%B %Y"), "bestsellers": best,
                             "in_stock": [{"title": p["title"], "price": inr(p["price_paise"])} for p in products],
                             "common_questions": faqs, "website": srow["website_url"]}, default=str, ensure_ascii=False))
    async with tenant_session(tenant_id) as db:
        c = await db.one(
            "INSERT INTO campaigns (tenant_id, kind, name, goal, channel, audience, message, subject) "
            "VALUES (:t,'newsletter',:n,:g,'email',:a,:m,:s) RETURNING *",
            t=tenant_id, n=f"Newsletter · {date.today().strftime('%B %Y')}", g="Monthly newsletter: bestsellers, a tip, what's new",
            a={"segment": "all"}, m=n.body, s=n.subject)
        await db.exec("INSERT INTO lifecycle_events (tenant_id, kind, ref, summary, data) VALUES (:t,'newsletter',:r,:s,:d) "
                      "ON CONFLICT (tenant_id, kind, ref) DO UPDATE SET data=:d",
                      t=tenant_id, r=month, s=f"Newsletter drafted: {n.subject}", d={"campaign_id": str(c["id"])})
    return c


# ---------------------------------------------------------------------------
# One scheduler job ("tick", a few times a day) decides what's due — keeps Cloud Scheduler inside its free tier
# and lets the service scale to zero between runs.
# ---------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))


async def tick(tenant_id: str, now_ist: datetime | None = None) -> dict:
    from app import insights

    t = now_ist or datetime.now(IST)
    out: dict = {"lifecycle": await run_lifecycle(tenant_id)}  # {"paused": True} while automations are paused
    if t.hour == 21 and not settings.automations_paused:
        out["daily_summary"] = bool(await daily_summary(tenant_id))
    if t.weekday() == 0 and t.hour < 12:
        async with tenant_session(tenant_id) as db:
            if not await db.one("SELECT 1 FROM insights_reports WHERE created_at > now() - interval '20 hours'"):
                await insights.build_gap_suggestions(db, tenant_id)
                await insights.weekly_report(db, tenant_id)
                out["weekly_report"] = True
    if t.day == 1 and not settings.automations_paused:
        out["newsletter"] = bool(await draft_newsletter(tenant_id))
    return out


def queue_lifecycle(tenant_id: str) -> None:
    enqueue(run_lifecycle, tenant_id, name=f"lifecycle-{tenant_id}", queue="lifecycle")

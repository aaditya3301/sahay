"""Sarvam Voice Agents adapter: Sarvam rents the phone line and does the ears and mouth (Saaras STT, Bulbul TTS,
barge-in); our platform stays the brain (business data, tools, policy gate, timeline).

One Sarvam agent per business, created once in Sarvam's dashboard from our template (Agent Studio shows the prompt,
hook URLs and a cURL per tool to paste in). Per call:

  on-start hook  → POST /v1/voice/sarvam/start            we find the business (its secret key) and the caller
                                                           (phone), preload orders/tickets/lead, return agent variables
  API tools      → POST /v1/voice/sarvam/tools/{name}      same tools + policy gate as chat; short speakable "result"
  on-end hook /  → POST /v1/voice/sarvam/webhook?k=...     transcript → customer timeline, summary, ticket, next step
  call webhook
  outbound       → Sarvam instant-outbound API             COD confirmations, callbacks, check-ins

Switching to Bolna or Naaradh later only replaces this file.
"""

import json
import logging
from datetime import timedelta

import httpx
import jwt

from app.config import settings
from app.core import norm_phone, now
from app.db import tenant_session

log = logging.getLogger("sahay.sarvam")
OUTBOUND_URL = "https://apps.sarvam.ai/api/outbounds/v1/orgs/{org}/workspaces/{ws}/outbounds"

AGENT_PROMPT_TEMPLATE = """You are {{agent_name}}, the phone agent of {{business_name}}.
{{persona}}

What we know about this caller (looked up before the call): {{caller_context}}

Rules:
- Speak {{language}}. One or two short sentences per turn. Repeat numbers back to confirm.
- Orders: the orders in "what we know" were placed with the number they're calling from, so help with them directly.
  For any other order, ask for the order ID and call verify_order (pass the mobile number they ordered with if it's a different phone);
  share details or change it only if it matches, and never say whether an order exists before that.
- For stock, prices and changes, call the tools. Never promise what a tool didn't confirm.
- The tools enforce the business's rules: if a tool says it needs approval or is denied, say so politely and offer the alternative.
- Never read links aloud: use send_link_whatsapp (it sends on Telegram) and tell them to check Telegram.
- If they ask for a person, are very upset, or it's legal/sensitive: use transfer_to_human.
Greeting: {{greeting}}"""


def hook_token(tenant_id: str) -> str:
    return jwt.encode({"typ": "sarvam_hook", "t": tenant_id, "iat": int(now().timestamp())}, settings.jwt_secret, "HS256")


def read_hook_token(token: str | None) -> str | None:
    try:
        d = jwt.decode(token or "", settings.jwt_secret, ["HS256"])
        return d["t"] if d.get("typ") == "sarvam_hook" else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The platform's own Sarvam agent and phone number, shared by every business. Its hooks carry ?k=<VOICE_PLATFORM_KEY>
# instead of a business key, and each call goes to the business that knows the caller.
# ---------------------------------------------------------------------------
def is_platform_key(k: str | None) -> bool:
    import hmac

    return bool(settings.voice_platform_key and k and hmac.compare_digest(k, settings.voice_platform_key))


def platform_urls() -> dict:
    base, k = settings.public_base_url.rstrip("/"), settings.voice_platform_key or "<VOICE_PLATFORM_KEY>"
    return {"on_start": f"{base}/v1/voice/sarvam/start?k={k}", "tools": f"{base}/v1/voice/sarvam/tools/{{tool}}?k={k}",
            "webhook": f"{base}/v1/voice/sarvam/webhook?k={k}"}


async def route_call(body: dict) -> str:
    """Which business a call on the platform number belongs to: the one it started with (for tool calls and the end of
    the call), else the business the caller is a customer of, else the business that owns the dialled number, else
    VOICE_DEFAULT_TENANT."""
    from app import routing
    from app.db import system_session

    refs = [str(r) for r in (_pick(body, "call_ref"), _pick(body, "interaction_id"), _pick(body, "attempt_id"),
                             (body.get("metadata") or {}).get("call_id")) if r]
    async with system_session() as db:
        if refs and (tid := await db.val("SELECT tenant_id FROM voice_call_routes WHERE ref = ANY(:r) LIMIT 1", r=refs)):
            return str(tid)
    caller = norm_phone(_pick(body, "user_phone_number", "user_identifier", "phone", "caller", "from"))
    if caller and (tid := await routing.business_for(phone=caller)):
        return tid
    dialled = norm_phone(_pick(body, "agent_phone_number", "to")) or norm_phone(settings.sarvam_agent_phone)
    async with system_session() as db:
        if dialled and (tid := await db.val("SELECT tenant_id FROM channel_routes WHERE channel='voice' AND external_key=:k", k=dialled)):
            return str(tid)
        if settings.voice_default_tenant and (tid := await db.val("SELECT id FROM tenants WHERE slug=:s", s=settings.voice_default_tenant)):
            return str(tid)
    raise ValueError("No business knows this caller, and no default business is set (VOICE_DEFAULT_TENANT).")


async def remember_call(tenant_id: str, *refs) -> None:
    """Tool calls and the end-of-call webhook of a platform call find their business through these refs."""
    from app.db import system_session

    async with system_session() as db:
        for r in {str(x) for x in refs if x}:
            await db.exec("INSERT INTO voice_call_routes (ref, tenant_id) VALUES (:r,:t) ON CONFLICT (ref) DO NOTHING", r=r, t=tenant_id)


def urls(tenant_id: str) -> dict:
    base = settings.public_base_url.rstrip("/")
    return {"on_start": f"{base}/v1/voice/sarvam/start", "tools": f"{base}/v1/voice/sarvam/tools/{{tool}}",
            "webhook": f"{base}/v1/voice/sarvam/webhook?k={hook_token(tenant_id)}"}


def _pick(body: dict, *keys: str):
    for k in keys:
        v = body.get(k)
        if v is None and isinstance(body.get("call_context"), dict):
            v = body["call_context"].get(k)
        if v not in (None, ""):
            return v
    return None


# ---------------------------------------------------------------------------
async def call_for_interaction(tenant_id: str, interaction_id: str | None, phone: str | None, create: bool = True) -> str | None:
    from app import voice

    if interaction_id:
        async with tenant_session(tenant_id) as db:
            row = await db.one("SELECT id FROM voice_calls WHERE context->>'interaction_id'=:i OR context->>'attempt_id'=:i ORDER BY created_at DESC LIMIT 1",
                               i=str(interaction_id))
        if row:
            return str(row["id"])
    if not create:
        return None
    started = await voice.start_call(tenant_id, direction="inbound", from_number=phone, to_number=None, purpose="inbound")
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id",
                      x={"interaction_id": str(interaction_id or ""), "provider": "sarvam"}, id=started["call_id"])
    await remember_call(tenant_id, started["call_id"], interaction_id)
    return started["call_id"]


def variables(started: dict, prof: dict, business: str) -> dict:
    """Flat agent variables for Sarvam (strings only)."""
    ctx = started.get("context") or {}
    tickets = ctx.get("open_tickets") or []
    bits = []
    if ctx.get("name"):
        bits.append(f"Name {ctx['name']}{' (VIP)' if ctx.get('vip') else ''}.")
    orders = ctx.get("recent_orders") or []
    if orders:  # placed with the number they're calling from: their own, so the agent can talk about them directly
        bits.append("Their latest orders (from this number): " + " ".join(
            f"#{o['order_ref']} {o['items']}, {o['status'].replace('_', ' ')}"
            + (f", expected {o['expected_delivery']}" if o.get("expected_delivery") else "") + "." for o in orders))
    for t in tickets[:2]:
        bits.append(f"Open ticket #{t['number']}" + (f" ({t['intent']})" if t.get("intent") else "") + (f": {t['summary']}" if t.get("summary") else "") + ".")
    if ctx.get("lead"):
        bits.append(f"Sales lead, stage {ctx['lead'].get('stage')}, interest: {ctx['lead'].get('interest')}.")
    return {
        "call_ref": started["call_id"], "business_name": business, "agent_name": prof.get("agent_name") or "Sahay",
        "persona": prof.get("persona") or "", "language": {"hi": "simple Hindi", "en": "Indian English"}.get(prof["voice"].get("language"), "Hinglish"),
        "greeting": started.get("greeting") or "", "customer_name": (ctx.get("name") or "").split(" ")[0],
        "caller_context": " ".join(bits) or "New caller, nothing on file.", "last_order_ref": orders[0]["order_ref"] if orders else "",
        "last_order_status": orders[0]["status"].replace("_", " ") if orders else "", "open_ticket": str(tickets[0]["number"]) if tickets else "",
        "known_customer": "yes" if ctx.get("known_customer") else "no",
    }


async def on_start(tenant_id: str, body: dict) -> dict:
    from app import voice
    from app.profile import load_profile

    phone = norm_phone(_pick(body, "user_phone_number", "user_identifier", "phone", "caller", "from"))
    interaction = _pick(body, "interaction_id", "call_id")
    existing = await call_for_interaction(tenant_id, interaction, phone, create=False)
    started = await voice.start_call(tenant_id, direction="inbound", from_number=phone, to_number=_pick(body, "agent_phone_number", "to"),
                                     purpose="inbound", call_id=existing)
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id",
                      x={"interaction_id": str(interaction or ""), "provider": "sarvam"}, id=started["call_id"])
        prof = await load_profile(db)
        business = await db.val("SELECT name FROM tenants WHERE id=:t", t=tenant_id)
    await remember_call(tenant_id, started["call_id"], interaction)
    return variables(started, prof, business)


def speakable(name: str, out: dict) -> str:
    """One short line the voice agent can say."""
    if not isinstance(out, dict):
        return str(out)[:300]
    if out.get("error"):
        return f"Sorry, I couldn't do that right now ({str(out['error'])[:80]})."
    if out.get("status") in ("succeeded", "pending_approval", "denied", "failed") and out.get("action_id"):
        return {"succeeded": "Done, it's completed.", "pending_approval": "I've sent it to the team for approval; they'll update you soon.",
                "denied": "That isn't allowed under our policy." + (f" I can offer: {out['offer_instead']}." if out.get("offer_instead") else ""),
                "failed": "That didn't go through; a teammate will follow up."}[out["status"]]
    if "match" in out:  # verify_order
        if not out["match"]:
            return out.get("note") or "That order ID doesn't match."
        o = out["order"]
        return (f"Verified. Order {o['order_ref']}: {', '.join(i['title'] for i in o['line_items'][:2])}, {o['status']}"
                + (f", expected {o['expected_delivery']}" if o.get("expected_delivery") else "")
                + (f", delivered {o['delivered_at'][:10]}" if o.get("delivered_at") else "") + ".")
    if "orders_on_file" in out:
        return out.get("note") or ""
    if "orders" in out:
        if not out["orders"]:
            return out.get("note") or "I couldn't find orders for this number."
        return " ".join(f"Order {o['order_ref']}: {', '.join(i['title'] for i in o['line_items'][:2])}, {o['status']}"
                        + (f", expected {o['expected_delivery']}" if o.get("expected_delivery") else "") + "." for o in out["orders"][:3])
    if "order_ref" in out and "status" in out:
        return (f"Order {out['order_ref']} is {out['status']}" + (f" with {out['courier']}" if out.get("courier") else "")
                + (f", expected {out['expected_delivery']}" if out.get("expected_delivery") else "") + ".")
    if "products" in out:
        return " ".join(f"{p['title']} at {int(p['price_inr'])} rupees{'' if p['in_stock'] else ' (out of stock)'}." for p in out["products"][:3])
    if "results" in out:
        return " ".join(r["content"][:220] for r in out["results"][:2]) or "I don't have that information."
    if out.get("note"):
        return out["note"]
    return json.dumps(out, default=str)[:300]


async def tool(tenant_id: str, name: str, body: dict) -> dict:
    from app import voice

    phone = norm_phone(_pick(body, "user_phone_number", "user_identifier", "phone"))
    call_id = _pick(body, "call_ref") or await call_for_interaction(tenant_id, _pick(body, "interaction_id"), phone)
    args = {k: v for k, v in body.items() if k not in ("interaction_id", "user_identifier", "user_phone_number", "call_ref", "call_context", "transcript", "call_length")}
    out = await voice.run_tool(tenant_id, call_id, name, args)
    return {"result": speakable(name, out), "data": json.loads(json.dumps(out, default=str))}


async def on_end(tenant_id: str, body: dict) -> dict:
    from app import voice

    meta = body.get("metadata") or {}
    phone = norm_phone(_pick(body, "user_phone_number", "user_identifier"))
    call_id = meta.get("call_id") or await call_for_interaction(tenant_id, _pick(body, "interaction_id", "attempt_id"), phone)
    turns = body.get("interaction_transcript") or body.get("transcript") or []
    transcript = [{"role": "caller" if t.get("role") in ("user", "caller") else "agent",
                   "text": t.get("indic_text") or t.get("en_text") or t.get("text") or ""} for t in turns if isinstance(t, dict)]
    status = "completed" if transcript else "no_answer"
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id",
                      x={"duration_s": body.get("duration"), "sarvam_variables": body.get("output_agent_variables") or {}}, id=call_id)
    return await voice.end_call(tenant_id, call_id, transcript or None, status)


# ---------------------------------------------------------------------------
async def place_call(tenant_id: str, call_id: str) -> dict:
    """Ask Sarvam to dial a queued outbound call (COD confirmation, callback, check-in)."""
    from app import voice
    from app.channels import integration
    from app.profile import load_profile

    async with tenant_session(tenant_id) as db:
        cfg = await integration(db, "sarvam")
        prof = await load_profile(db)
        call = await db.one("SELECT * FROM voice_calls WHERE id=:id", id=call_id)
        business = await db.val("SELECT name FROM tenants WHERE id=:t", t=tenant_id)
    need = ["api_key", "org_id", "workspace_id", "app_id", "connection_id", "agent_phone_number"]
    if missing := [k for k in need if not cfg.get(k)]:
        raise ValueError(f"Sarvam isn't fully connected (missing {', '.join(missing)})")
    started = await voice.start_call(tenant_id, direction="outbound", from_number=None, to_number=call["to_number"], purpose=call["purpose"], call_id=call_id)
    body = {
        "app_config": {"app_id": cfg["app_id"], "app_version": int(cfg.get("app_version") or 1),
                       "connection_config": {"connection_id": cfg["connection_id"], "agent_phone_number": cfg["agent_phone_number"]},
                       "agent_variables": {**variables(started, prof, business), "call_purpose": call["purpose"],
                                           "call_goal": json.dumps(call["context"] or {}, default=str)[:400]}},
        "user_config": {"user_phone_number": call["to_number"]},
        "webhook_config": {"url": urls(tenant_id)["webhook"], "metadata": {"call_id": call_id, "purpose": call["purpose"]}},
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(OUTBOUND_URL.format(org=cfg["org_id"], ws=cfg["workspace_id"]), json=body,
                         headers={"X-API-Key": cfg["api_key"], "Content-Type": "application/json"})
    if r.status_code >= 300:
        async with tenant_session(tenant_id) as db:
            await db.exec("UPDATE voice_calls SET status='failed', context = context || :x WHERE id=:id", x={"sarvam_error": r.text[:300]}, id=call_id)
        raise RuntimeError(f"Sarvam refused the call: {r.text[:200]}")
    attempt = r.json().get("attempt_id")
    await remember_call(tenant_id, call_id, attempt)
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET status='ringing', context = context || :x WHERE id=:id",
                      x={"attempt_id": attempt, "provider": "sarvam"}, id=call_id)
    return {"attempt_id": attempt, "call_id": call_id}


async def queue_test_call(tenant_id: str, phone: str, purpose: str = "check_in") -> str:
    async with tenant_session(tenant_id) as db:
        cust = await db.one("SELECT customer_id FROM customer_identities WHERE kind='phone' AND value=:p", p=norm_phone(phone))
        row = await db.one("INSERT INTO voice_calls (tenant_id, direction, purpose, to_number, customer_id, status, context, scheduled_for) "
                           "VALUES (:t,'outbound',:pu,:p,:c,'queued',:x, now()) RETURNING id",
                           t=tenant_id, pu=purpose, p=norm_phone(phone), c=cust and cust["customer_id"], x={"topic": "test call from the dashboard"})
    return str(row["id"])


def template(tenant_id: str, tool_specs: list[dict], secret_hint: str) -> dict:
    """Everything the owner pastes into Sarvam once: prompt, variables, hook URLs and a cURL per tool."""
    u = urls(tenant_id)
    curls = []
    for t in tool_specs:
        example = {k: f"<{k}>" for k in (t["parameters"].get("properties") or {})}
        example["interaction_id"] = "@Interaction ID"
        example["user_identifier"] = "@User Identifier"
        curls.append({"name": t["name"], "description": t["description"],
                      "curl": f"curl -X POST '{u['tools'].format(tool=t['name'])}' -H 'Authorization: Bearer {secret_hint}' "
                              f"-H 'Content-Type: application/json' -d '{json.dumps(example)}'"})
    return {"prompt": AGENT_PROMPT_TEMPLATE, "urls": u, "tools": curls,
            "variables": ["call_ref", "business_name", "agent_name", "persona", "language", "greeting", "customer_name", "caller_context",
                          "last_order_ref", "last_order_status", "open_ticket", "known_customer"],
            "response_template": "{{result}}", "max_wait_s": 5, "expires": str(now() + timedelta(days=3650))}


if __name__ == "__main__":  # `uv run python -m app.voice_sarvam`: the hook URLs to paste into the platform's Sarvam agent
    print(json.dumps(platform_urls(), indent=2))

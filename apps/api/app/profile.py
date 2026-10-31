"""The Agent Profile: one per business, loaded by every channel (widget, Reply Now chat, Telegram,
email, voice). It holds the persona, which skills are on, custom tools, sales and voice settings.

Skills replace a free-roaming agent: triage picks the intent, the intent picks one or more skills,
and each skill limits which tools the resolver may call and adds its own short playbook.
"""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

SKILLS: dict[str, dict] = {
    # sales
    "product_finder": {
        "label": "Product finder", "group": "sales",
        "description": "Recommends products that fit what the visitor wants, with live price and stock.",
        "tools": ["search_products", "get_product", "check_stock"],
        "intents": ["product_question", "pre_sales", "bulk_order"],
        "guide": "Recommend 1-3 products that fit the need. Always take price and stock from search_products/get_product in THIS turn; never quote a price from memory or the knowledge base.",
    },
    "price_offers": {
        "label": "Price & offers", "group": "sales",
        "description": "Answers price, discount and offer questions from live data and the knowledge base.",
        "tools": ["get_product", "search_products"],
        "intents": ["pre_sales", "coupon_issue", "bulk_order"],
        "guide": "Quote live prices only. Mention offers or discounts only if the knowledge base states them.",
    },
    "capture_lead": {
        "label": "Capture lead", "group": "sales",
        "description": "When a visitor shows buying interest, asks for name and phone/email naturally and creates a lead.",
        "tools": ["capture_lead"],
        "intents": ["pre_sales", "product_question", "bulk_order", "callback_request"],
        "guide": "When the visitor shows real buying interest (bulk or corporate order, wants a quote, wants to be contacted, is comparing seriously), ask naturally for their name and a phone or email, then call capture_lead with what they want. Don't ask before there is interest and never ask twice.",
    },
    "book_call": {
        "label": "Book a call", "group": "sales",
        "description": "Offers and books a callback with the team for questions that need a human sales conversation.",
        "tools": ["book_callback"],
        "intents": ["callback_request", "bulk_order"],
        "guide": "Offer a callback when the visitor asks for one or needs a custom quote. Get a phone number and a preferred time, then call book_callback.",
    },
    # support
    "order_status": {
        "label": "Order status", "group": "support",
        "description": "Finds the customer's orders, tracks shipments and explains delivery dates.",
        "tools": ["list_customer_orders", "get_order", "track_shipment", "request_verification"],
        "intents": ["order_status", "delivery_delay", "cod_query", "refund_status"],
        "guide": "Look up the customer's orders yourself and give the live status and ETA.",
    },
    "returns_claims": {
        "label": "Returns & claims", "group": "support",
        "description": "Damaged, wrong or missing items, returns and refunds, with photo checks and the policy gate.",
        "tools": ["list_customer_orders", "get_order", "analyze_attachment", "check_policy", "create_replacement",
                  "create_return", "issue_refund", "issue_coupon", "request_verification"],
        "intents": ["damaged_item", "wrong_item", "missing_item", "return_request", "exchange_size", "refund_request", "refund_status"],
        "guide": "For photo claims analyze the attachment first. Let the policy engine decide; offer its alternative if denied.",
    },
    "order_changes": {
        "label": "Order changes", "group": "support",
        "description": "Cancellations, address changes, delivery rescheduling and COD confirmation.",
        "tools": ["list_customer_orders", "get_order", "check_policy", "cancel_order", "update_shipping_address",
                  "reschedule_delivery", "confirm_cod", "issue_coupon", "request_verification"],
        "intents": ["cancel_order", "address_change", "cod_query"],
        "guide": "Only change orders that belong to this customer.",
    },
    "general_help": {
        "label": "General help", "group": "support",
        "description": "Answers policy and FAQ questions from the knowledge base and hands over to a human when needed.",
        "tools": [],
        "intents": [],
        "guide": "",
    },
}
ALWAYS_TOOLS = ["search_kb", "get_customer_profile", "escalate_to_human"]
SAVE_THE_ORDER_GUIDE = (
    "Save-the-order: before cancelling an order that has not shipped, ask once if a fix would help instead "
    "(faster delivery, a different size/variant, or a small goodwill coupon via issue_coupon). If they still want to cancel, cancel it without arguing."
)

DEFAULT_SALES = {
    "capture_leads": True, "save_the_order": True, "icp": "", "offer": "", "booking_url": "", "sender_name": "",
    "follow_up_days": 3, "cart_reminders": True, "cart_reminder_hours": 1, "newsletter": True,
    "payee_names": [], "upi_ids": [],  # receipts must be paid to these (plus the business name) to pass the authenticity check
}
DEFAULT_VOICE = {
    "enabled": True, "provider": "sarvam", "voice": "female-hinglish", "greeting": "", "business_hours": "10:00-19:00 Mon-Sat",
    "transfer_number": "", "numbers": [], "language": "hinglish", "webhook_url": "",
}
DEFAULT_WIDGET = {"allowed_domains": [], "require_otp_for_orders": True, "launcher_text": "Chat with us", "theme": "classic",
                  "bot_check": True, "daily_message_cap": 400, "session_message_cap": 40}


def new_secret_key(slug: str) -> str:
    return f"sk_{re.sub(r'[^a-z0-9]', '', slug)[:12]}_{secrets.token_hex(16)}"


async def register_secret(db, secret: str, old: str | None = None) -> None:
    """The secret key authenticates the business's server (voice pipeline, custom tools). Stored hashed in the global routing table."""
    if old:
        await db.exec("DELETE FROM channel_routes WHERE channel='secret' AND external_key=:k", k=hashlib.sha256(old.encode()).hexdigest())
    await db.exec("INSERT INTO channel_routes (channel, external_key, tenant_id) VALUES ('secret', :k, :t) ON CONFLICT DO NOTHING",
                  k=hashlib.sha256(secret.encode()).hexdigest(), t=db.tenant_id)


async def load_profile(db) -> dict:
    row = await db.one("SELECT * FROM agent_profiles")
    if not row:
        slug = await db.val("SELECT slug FROM tenants WHERE id=:t", t=db.tenant_id) or "brand"
        row = await db.one(
            "INSERT INTO agent_profiles (tenant_id, secret_key) VALUES (:t, :k) ON CONFLICT (tenant_id) DO UPDATE SET tenant_id=excluded.tenant_id RETURNING *",
            t=db.tenant_id, k=new_secret_key(slug),
        )
        await register_secret(db, row["secret_key"])
    p = dict(row)
    p["skills"] = {k: True for k in SKILLS}  # all skills are always on: there is no switch for them in the UI
    p["sales"] = {**DEFAULT_SALES, **(row["sales"] or {}), "capture_leads": True, "save_the_order": True}
    p["voice"] = {**DEFAULT_VOICE, **(row["voice"] or {})}
    p["widget"] = {**DEFAULT_WIDGET, **(row["widget"] or {})}
    p["custom_tools"] = [t for t in (row["custom_tools"] or []) if t.get("name")]
    return p


def public_profile(p: dict) -> dict:
    """What the dashboard may see (secret key masked)."""
    out = {k: v for k, v in p.items() if k not in ("secret_key", "tenant_id")}
    sk = p.get("secret_key") or ""
    out["secret_key_hint"] = f"{sk[:8]}…{sk[-4:]}" if sk else None
    out["skill_catalog"] = [{"id": k, **{x: s[x] for x in ("label", "group", "description")}, "tools": s["tools"]} for k, s in SKILLS.items()]
    return out


# ---------------------------------------------------------------------------
# Skill routing
# ---------------------------------------------------------------------------
SALES_INTENTS = {"pre_sales", "product_question", "bulk_order", "callback_request"}


def pick_skills(intent: str | None, profile: dict) -> list[str]:
    on = {k for k, v in profile["skills"].items() if v}
    if not profile["sales"].get("capture_leads"):
        on.discard("capture_lead")
    picked = [k for k in SKILLS if k in on and intent in SKILLS[k]["intents"]]
    if not picked:  # messy or unmapped intents (complaints, payment, other): every support skill
        picked = [k for k in SKILLS if k in on and SKILLS[k]["group"] == "support"]
    if "general_help" in on and "general_help" not in picked:
        picked.append("general_help")
    return picked


def tools_for(skills: list[str], profile: dict, channel: str, verified: bool) -> list[str]:
    names = list(ALWAYS_TOOLS)
    for s in skills:
        names += SKILLS[s]["tools"]
    if any(SKILLS[s]["group"] == "support" for s in skills) or any(s in skills for s in ("product_finder",)):
        names += ["list_customer_orders", "get_order", "verify_order"]
    if channel != "webchat" or verified:
        names = [n for n in names if n != "request_verification"]
    if channel == "voice":
        names += ["verify_order", "send_link_whatsapp", "transfer_to_human"]
    names += [custom_tool_name(t) for t in profile["custom_tools"] if t.get("enabled", True)]
    return list(dict.fromkeys(names))


def skills_prompt(skills: list[str], profile: dict, intent: str | None) -> str:
    lines = [f"- {SKILLS[s]['label']}: {SKILLS[s]['guide']}" for s in skills if SKILLS[s]["guide"]]
    if profile["sales"].get("save_the_order") and intent == "cancel_order":
        lines.append("- " + SAVE_THE_ORDER_GUIDE)
    if profile["sales"].get("booking_url") and "book_call" in skills:
        lines.append(f"- Self-serve booking link (share it if they prefer to pick a slot): {profile['sales']['booking_url']}")
    for t in profile["custom_tools"]:
        if t.get("enabled", True) and t.get("when_to_use"):
            lines.append(f"- {custom_tool_name(t)}: {t['when_to_use']}")
    return "\n".join(lines) or "- (none)"


def persona_block(profile: dict, srow: dict) -> str:
    name = profile.get("agent_name") or "Sahay"
    extra = f"\nPersona notes from the owner: {profile['persona']}" if profile.get("persona") else ""
    return f"Your name is {name}, the AI teammate of {srow['tenant_name']} (sales and support).{extra}"


# ---------------------------------------------------------------------------
# Custom tools: the business plugs in its own HTTPS endpoint
# ---------------------------------------------------------------------------
def custom_tool_name(t: dict) -> str:
    return "custom_" + re.sub(r"[^a-z0-9_]", "_", t["name"].lower()).strip("_")[:40]


def custom_tool_specs(profile: dict) -> dict[str, dict]:
    out = {}
    for t in profile["custom_tools"]:
        if not t.get("enabled", True):
            continue
        props = {p["name"]: {"type": p.get("type") or "string", "description": p.get("description", "")} for p in t.get("parameters") or [] if p.get("name")}
        req = [p["name"] for p in t.get("parameters") or [] if p.get("required")]
        out[custom_tool_name(t)] = {"kind": "read", "description": t.get("description") or t["name"],
                                    "parameters": {"type": "object", "properties": props, "required": req}, "custom": t}
    return out


def _safe_url(url: str) -> bool:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return False
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except Exception:
        return False
    return True


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def call_custom_tool(profile: dict, spec: dict, args: dict, customer: dict) -> dict:
    t = spec["custom"]
    if not _safe_url(t.get("url", "")):
        return {"error": "This tool's URL must be a public https:// address."}
    body = json.dumps({"tool": t["name"], "args": args, "customer": customer}, default=str).encode()
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.request((t.get("method") or "POST").upper(), t["url"], content=body,
                                headers={"Content-Type": "application/json", "X-Sahay-Signature": sign(profile["secret_key"] or "", body)})
        data: Any = r.json() if "json" in r.headers.get("content-type", "") else r.text
        if not isinstance(data, str) and len(json.dumps(data, default=str)) > 3000:
            data = json.dumps(data, default=str)[:3000]
        return {"status": r.status_code, "data": data[:3000] if isinstance(data, str) else data}
    except Exception as e:
        return {"error": f"tool call failed: {str(e)[:160]}"}


def verify_user_hash(secret: str | None, value: str | None, given: str | None) -> bool:
    """Identity verification for logged-in shoppers: the business's server signs the email (or phone)."""
    if not (secret and value and given):
        return False
    expected = hmac.new(secret.encode(), value.strip().lower().encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given.strip().lower())

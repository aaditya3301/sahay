"""One action layer for everyone (AI resolver, human in the inbox, owner on WhatsApp).

Read tools return data scoped to the conversation's customer. Action tools never execute
directly: they compute facts server-side, run the policy gate, and only then execute
(allow), create an approval (require_approval), or refuse (deny).
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app import kb, policy as pol
from app.commerce import MockCommerce, get_commerce
from app.core import audit, inr, norm_email, norm_phone, security_event


@dataclass
class ToolContext:
    db: Any
    tenant_id: str
    ticket: dict
    customer: dict
    conversation: dict | None = None
    actor: str = "ai"  # ai | human | owner | system
    actor_name: str | None = None
    dry_run: bool = False
    commerce: MockCommerce | None = None
    kb_hits: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    escalation: dict | None = None
    identities: dict = field(default_factory=dict)
    profile: dict | None = None
    ui: dict = field(default_factory=dict)  # rich bits for the widget: product cards, verify form
    lead_id: str | None = None
    voice_call_id: str | None = None
    seen: list[str] = field(default_factory=list)

    async def shop(self) -> MockCommerce:
        if self.commerce is None:
            self.commerce = await get_commerce(self.db)
        return self.commerce


# ---------------------------------------------------------------------------
# Declarations for Gemini function calling
# ---------------------------------------------------------------------------
def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S = {"type": "string"}
N = {"type": "number"}

TOOL_SPECS: dict[str, dict] = {
    # read tools
    "search_kb": {"kind": "read", "description": "Search this company's knowledge base (policies, FAQs). Returns cited snippets.",
                  "parameters": _obj({"query": S}, ["query"])},
    "get_customer_profile": {"kind": "read", "description": "Profile of the customer in this conversation: name, verified identities, VIP, recent tickets.",
                             "parameters": _obj({})},
    "list_customer_orders": {"kind": "read", "description": "Recent orders of THIS customer (matched by their phone/email).",
                             "parameters": _obj({})},
    "get_order": {"kind": "read", "description": "Details of one order of this customer, including shipment status and delivery date. "
                  "Orders placed and paid on the shop's website (ids like LL-7KQ3XD) open with the order id alone; ask for it.",
                  "parameters": _obj({"order_ref": S}, ["order_ref"])},
    "verify_order": {"kind": "read", "description": "Check that an order ID the customer gives is theirs: it must match the number they are calling "
                     "from, or the mobile number they say they ordered with (pass it as phone). On calls, do this before sharing any order "
                     "details or changing an order, and only continue if match is true.",
                     "parameters": _obj({"order_ref": S, "phone": S}, ["order_ref"])},
    "track_shipment": {"kind": "read", "description": "Courier, AWB, live shipment status and ETA for an order.",
                       "parameters": _obj({"order_ref": S}, ["order_ref"])},
    "check_stock": {"kind": "read", "description": "Stock available for a product SKU.", "parameters": _obj({"sku": S}, ["sku"])},
    "analyze_attachment": {"kind": "read", "description": "Analyze a customer photo against the ordered product: damage, product match, fraud signals.",
                           "parameters": _obj({"attachment_id": S, "order_ref": S}, ["attachment_id", "order_ref"])},
    "check_policy": {"kind": "read", "description": "Preview whether an action would be allowed, need approval, or be denied — without doing it.",
                     "parameters": _obj({"action": {"type": "string", "enum": list(pol.ACTIONS)}, "order_ref": S, "amount_inr": N}, ["action"])},
    # action tools
    "create_replacement": {"kind": "action", "description": "Send a free replacement for damaged/wrong/missing items of an order.",
                           "parameters": _obj({"order_ref": S, "skus": {"type": "array", "items": S}, "reason": S}, ["order_ref", "reason"])},
    "issue_refund": {"kind": "action", "description": "Refund money for an order. amount_inr defaults to the refundable amount.",
                     "parameters": _obj({"order_ref": S, "amount_inr": N, "reason": S}, ["order_ref", "reason"])},
    "cancel_order": {"kind": "action", "description": "Cancel an order that has not shipped yet.",
                     "parameters": _obj({"order_ref": S, "reason": S}, ["order_ref", "reason"])},
    "update_shipping_address": {"kind": "action", "description": "Change the delivery address of an unshipped order.",
                                "parameters": _obj({"order_ref": S, "address": S, "pincode": S}, ["order_ref", "address"])},
    "issue_coupon": {"kind": "action", "description": "Give a small single-use apology/goodwill coupon.",
                     "parameters": _obj({"value_inr": N, "reason": S}, ["value_inr", "reason"])},
    "confirm_cod": {"kind": "action", "description": "Mark a cash-on-delivery order as confirmed by the customer.",
                    "parameters": _obj({"order_ref": S}, ["order_ref"])},
    "reschedule_delivery": {"kind": "action", "description": "Reschedule delivery to a date (YYYY-MM-DD).",
                            "parameters": _obj({"order_ref": S, "date": S}, ["order_ref", "date"])},
    "create_return": {"kind": "action", "description": "Start a return / reverse pickup for items.",
                      "parameters": _obj({"order_ref": S, "skus": {"type": "array", "items": S}, "reason": S}, ["order_ref", "reason"])},
    # sales tools
    "search_products": {"kind": "read", "description": "Search the live product catalogue by need, occasion or keywords. Returns live price and stock.",
                        "parameters": _obj({"query": S}, ["query"])},
    "get_product": {"kind": "read", "description": "Live details of one product by SKU: price, stock, description.",
                    "parameters": _obj({"sku": S}, ["sku"])},
    "capture_lead": {"kind": "sales", "description": "Save this visitor as a sales lead once they show buying interest and shared a name and a phone or email.",
                     "parameters": _obj({"name": S, "phone": S, "email": S, "company": S, "interest": S, "notes": S}, ["interest"])},
    "book_callback": {"kind": "sales", "description": "Book a callback from the team. preferred_time is free text like 'tomorrow 5pm'.",
                      "parameters": _obj({"phone": S, "name": S, "preferred_time": S, "topic": S}, ["topic"])},
    "request_verification": {"kind": "read", "description": "Show the visitor a phone OTP form in the chat. Use when an unverified web visitor asks about their orders.",
                             "parameters": _obj({})},
    # voice-only tools
    "send_link_whatsapp": {"kind": "voice", "description": "Send details to the caller on Telegram instead of reading them aloud (order number, courier + AWB, a link that appeared in a tool or knowledge result). Never make up a URL.",
                           "parameters": _obj({"text": S}, ["text"])},
    "transfer_to_human": {"kind": "voice", "description": "Warm-transfer the call to a human with a one-line summary.",
                          "parameters": _obj({"summary": S}, ["summary"])},
    "escalate_to_human": {"kind": "escalate", "description": "Hand the ticket to a human with a short summary and your suggested decision. Use for sensitive cases, missing information, or anything outside policy.",
                          "parameters": _obj({"reason": S, "summary": S, "suggested_decision": S}, ["reason", "summary"])},
}


CORE_TOOLS = [n for n, s in TOOL_SPECS.items() if s["kind"] in ("read", "action", "escalate")
              and n not in ("search_products", "get_product", "request_verification")]


def gemini_tools(names: list[str] | None = None, extra: dict[str, dict] | None = None):
    """Tool declarations for the resolver. `names` limits them to the picked skills."""
    from google.genai import types

    specs = {**TOOL_SPECS, **(extra or {})}
    names = names or CORE_TOOLS
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(name=n, description=specs[n]["description"], parameters_json_schema=specs[n]["parameters"])
        for n in names if n in specs
    ])]


# ---------------------------------------------------------------------------
# Identity / ownership helpers
# ---------------------------------------------------------------------------
async def customer_identities(ctx: ToolContext) -> dict:
    if not ctx.identities:
        rows = await ctx.db.all("SELECT kind, value, verified FROM customer_identities WHERE customer_id=:c", c=ctx.customer["id"])
        def val(r):  # simulated identities are namespaced "<run>|<value>"
            return r["value"].split("|", 1)[1] if r["kind"].startswith("sim_") else r["value"]

        if (ctx.conversation or {}).get("channel") == "webchat":  # typed-in contacts are not proof; OTP/signed ones are
            rows = [r for r in rows if r["verified"] or r["kind"] == "webchat_visitor"]
        ctx.identities = {"phones": {val(r) for r in rows if r["kind"] in ("phone", "wa_id", "sim_phone")},
                          "emails": {val(r) for r in rows if r["kind"] in ("email", "sim_email")},
                          "verified": any(r["verified"] for r in rows)}
    return ctx.identities


async def call_state(ctx: ToolContext) -> dict:
    """Per-call memory on voice_calls.context: which order ids the caller has verified, and failed attempts."""
    if not ctx.voice_call_id:
        return {}
    return (await ctx.db.val("SELECT context FROM voice_calls WHERE id=:id", id=ctx.voice_call_id)) or {}


async def _save_call_state(ctx: ToolContext, **fields) -> None:
    if ctx.voice_call_id:
        await ctx.db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id", x=fields, id=ctx.voice_call_id)


MAX_VERIFY_FAILURES = 3


async def owned_order(ctx: ToolContext, order_ref: str) -> tuple[dict | None, bool]:
    """Returns (order, belongs_to_customer). Orders of other customers are never returned to the LLM."""
    shop = await ctx.shop()
    order = await shop.get_order(order_ref or "")
    if not order:
        return None, False
    if ctx.voice_call_id:  # on a call the caller ID alone isn't enough: the caller must have verified this order id
        return order, order["order_ref"].upper() in (await call_state(ctx)).get("verified_orders", [])
    ids = await customer_identities(ctx)
    belongs = (norm_phone(order.get("customer_phone")) in ids["phones"]) or (norm_email(order.get("customer_email")) in ids["emails"])
    return order, belongs


async def _customer_stats(ctx: ToolContext) -> dict:
    row = await ctx.db.one(
        "SELECT count(*) FILTER (WHERE status='succeeded' AND proposed_by='ai' AND type IN ('issue_refund','create_replacement','issue_coupon') AND created_at > now()-interval '30 days' AND NOT dry_run) AS auto30, "
        "count(*) FILTER (WHERE type IN ('create_replacement','issue_refund','create_return') AND created_at > now()-interval '90 days' AND NOT dry_run) AS claims90 "
        "FROM actions WHERE customer_id=:c", c=ctx.customer["id"],
    )
    return {"auto30": int(row["auto30"] or 0), "claims90": int(row["claims90"] or 0)}


async def _evidence(ctx: ToolContext) -> dict:
    rows = await ctx.db.all(
        "SELECT a.analysis, a.screening FROM attachments a JOIN messages m ON m.id=a.message_id WHERE m.ticket_id=:t "
        "AND (a.analysis IS NOT NULL OR a.screening IS NOT NULL)", t=ctx.ticket["id"],
    )
    if not rows:
        return {}
    verified = any((r["analysis"] or {}).get("photo_verified") for r in rows)
    reused = any((r["analysis"] or {}).get("reused_image") for r in rows)
    suspicious = any((r["screening"] or {}).get("verdict") in ("suspicious", "likely_fake") for r in rows)
    doc_ok = any((r["screening"] or {}).get("verdict") == "genuine" and (r["screening"] or {}).get("kind") in
                 ("payment_receipt", "upi_or_bank_screenshot", "invoice_or_bill") for r in rows)
    return {"evidence.photo_verified": verified and not suspicious, "evidence.reused_image": reused,
            "evidence.document_suspicious": suspicious, "evidence.document_verified": doc_ok}


async def compute_facts(ctx: ToolContext, action: str, params: dict) -> tuple[dict, dict | None, int]:
    facts: dict[str, Any] = {
        "ticket.intent": ctx.ticket.get("intent"),
        "ticket.sensitive": bool(ctx.ticket.get("sensitive")),
        "ticket.injection_suspected": bool(ctx.ticket.get("injection_suspected")),
        "customer.vip": bool(ctx.customer.get("vip")),
        "customer.fraud_score": float(ctx.customer.get("fraud_score") or 0),
        "customer.frustration": float(ctx.ticket.get("frustration_score") or 0),
    }
    stats = await _customer_stats(ctx)
    facts["customer.auto_actions_30d"] = stats["auto30"]
    facts["customer.claims_90d"] = stats["claims90"]
    facts.update(await _evidence(ctx))
    order = None
    value_paise = 0
    if params.get("order_ref"):
        order, belongs = await owned_order(ctx, params["order_ref"])
        facts["order.belongs_to_customer"] = belongs
        facts["customer.verified_for_order"] = belongs
        if order:
            facts["order.total_inr"] = order["total_inr"]
            facts["order.payment_method"] = order["payment_method"]
            facts["order.status"] = order["status"]
            facts["order.is_shipped"] = order["status"] in ("shipped", "in_transit", "delivered", "out_for_delivery")
            if order.get("days_since_delivery") is not None:
                facts["order.days_since_delivery"] = order["days_since_delivery"]
            skus = params.get("skus") or [li["sku"] for li in order["line_items"]]
            items = [li for li in order["line_items"] if li["sku"] in skus] or order["line_items"]
            shop = await ctx.shop()
            prod = await shop.get_product(items[0]["sku"]) if items else None
            if prod:
                facts["product.category"] = prod["category"]
            if action == "issue_refund":
                refundable = order["total_inr"] - order.get("refunded_inr", 0)
                amount = float(params.get("amount_inr") or refundable)
                value_paise = int(amount * 100)
            elif action in ("create_replacement", "create_return"):
                value_paise = sum(int(li.get("price_paise", 0)) * int(li.get("qty", 1)) for li in items)
            elif action == "cancel_order":
                value_paise = int(order["total_inr"] * 100)
        else:
            facts["order.belongs_to_customer"] = False
    if action == "issue_coupon":
        value_paise = int(float(params.get("value_inr") or 0) * 100)
    facts["action.value_inr"] = value_paise / 100
    return facts, order, value_paise


async def active_policy(db) -> pol.PolicyDoc:
    row = await db.one("SELECT rules FROM policies WHERE status='active' ORDER BY version DESC LIMIT 1")
    return pol.PolicyDoc.model_validate(row["rules"]) if row else pol.PolicyDoc()


def idem_key(ctx: ToolContext, action: str, params: dict) -> str:
    canon = json.dumps({k: params[k] for k in sorted(params) if k != "reason"}, sort_keys=True, default=str)
    return hashlib.sha256(f"{ctx.ticket['id']}|{action}|{canon}".encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
async def execute_action(db, action: dict, dry_run: bool = False) -> dict:
    """Run an approved action exactly once."""
    row = await db.one("SELECT * FROM actions WHERE id=:id FOR UPDATE", id=action["id"])
    if row["status"] == "succeeded":
        return row["result"] or {}
    if row["status"] not in ("approved",):
        raise ValueError(f"action not approved (status={row['status']})")
    await db.exec("UPDATE actions SET status='executing', updated_at=now() WHERE id=:id", id=row["id"])
    p = row["params"]
    try:
        if dry_run or row["dry_run"]:
            result = {"dry_run": True, "would_execute": row["type"], "params": p}
        else:
            shop = await get_commerce(db)
            t = row["type"]
            if t == "create_replacement":
                result = await shop.create_replacement(p["order_ref"], p.get("skus") or [], p.get("reason", ""))
            elif t == "issue_refund":
                result = await shop.refund(p["order_ref"], row["value_paise"] / 100, p.get("reason", ""))
            elif t == "cancel_order":
                result = await shop.cancel(p["order_ref"], p.get("reason", ""))
            elif t == "update_shipping_address":
                result = await shop.update_address(p["order_ref"], {"address": p.get("address"), "pincode": p.get("pincode")})
            elif t == "issue_coupon":
                result = await shop.create_discount(row["value_paise"] / 100, str(row["customer_id"]))
            elif t == "confirm_cod":
                result = await shop.tag_order(p["order_ref"], "cod_confirmed")
            elif t == "reschedule_delivery":
                result = await shop.reschedule(p["order_ref"], p["date"])
            elif t == "create_return":
                result = await shop.tag_order(p["order_ref"], "return_requested")
                result["pickup"] = "Reverse pickup will be scheduled within 48 hours"
            else:
                raise ValueError(f"unknown action {t}")
        await db.exec("UPDATE actions SET status='succeeded', result=:r, updated_at=now() WHERE id=:id", r=result, id=row["id"])
        await audit(db, "system", None, "action.executed", "action", row["id"], {"type": row["type"], "result": result})
        return result
    except Exception as e:
        await db.exec("UPDATE actions SET status='failed', error=:e, updated_at=now() WHERE id=:id", e=str(e)[:500], id=row["id"])
        raise


async def propose(ctx: ToolContext, action: str, params: dict) -> dict:
    """Create an action proposal, gate it, and execute/park/refuse it."""
    facts, order, value_paise = await compute_facts(ctx, action, params)
    doc = await active_policy(ctx.db)
    gate = pol.evaluate(action, facts, doc)
    key = idem_key(ctx, action, params)
    existing = await ctx.db.one("SELECT * FROM actions WHERE idempotency_key=:k", k=key)
    if existing:
        out = {"action_id": str(existing["id"]), "status": existing["status"], "duplicate": True,
               "decision": existing["decision"], "result": existing["result"]}
        ctx.actions.append({**out, "type": action, "params": params})
        return out
    status = {"allow": "approved", "require_approval": "pending_approval", "deny": "denied"}[gate.decision]
    row = await ctx.db.one(
        "INSERT INTO actions (tenant_id, ticket_id, customer_id, type, params, facts, decision, decision_reason, matched_rules, "
        "status, proposed_by, idempotency_key, dry_run, value_paise) VALUES (:t,:tk,:c,:ty,:p,:f,:d,:dr,:mr,:s,:pb,:k,:dry,:v) RETURNING *",
        t=ctx.tenant_id, tk=ctx.ticket["id"], c=ctx.customer["id"], ty=action, p=params, f=facts, d=gate.decision,
        dr="; ".join(gate.reasons), mr=gate.matched_rules, s=status, pb=ctx.actor, k=key, dry=ctx.dry_run, v=value_paise,
    )
    await audit(ctx.db, ctx.actor, ctx.actor_name, f"action.{gate.decision}", "action", row["id"],
                {"type": action, "params": params, "reasons": gate.reasons})
    out: dict[str, Any] = {"action_id": str(row["id"]), "decision": gate.decision, "reasons": gate.reasons}
    if gate.decision == "deny":
        out["status"] = "denied"
        if gate.deny_hint:
            out["offer_instead"] = gate.deny_hint
        if gate.invariant_hits:
            await security_event(ctx.db, "action_denied", f"{action} denied: {'; '.join(gate.reasons)}", ctx.ticket["id"],
                                 "high" if facts.get("ticket.injection_suspected") else "medium", {"params": params, "facts": facts})
    elif gate.decision == "allow":
        try:
            out["result"] = await execute_action(ctx.db, row, ctx.dry_run)
            out["status"] = "succeeded"
        except Exception as e:
            out["status"] = "failed"
            out["error"] = str(e)[:200]
    else:
        out["status"] = "pending_approval"
        out["note"] = "A team member must approve this. Tell the customer it is being reviewed; do not say it is done."
        from app.approvals import create_approval

        await create_approval(
            ctx.db, kind="action", ticket_id=ctx.ticket["id"], action_id=row["id"],
            summary=_approval_summary(ctx, action, params, order, value_paise),
            suggestion=f"Policy: {'; '.join(gate.reasons)}", dry_run=ctx.dry_run,
        )
    ctx.actions.append({**out, "type": action, "params": params, "value_inr": value_paise / 100})
    return out


def _approval_summary(ctx: ToolContext, action: str, params: dict, order: dict | None, value_paise: int) -> str:
    who = ctx.customer.get("display_name") or "Customer"
    parts = [f"{pol.ACTIONS.get(action, action)} for {who}"]
    if order:
        parts.append(f"#{order['order_ref']} · {inr(order['total_inr'] * 100)} ({order['payment_method'].upper()})")
    if value_paise:
        parts.append(f"value {inr(value_paise)}")
    if params.get("reason"):
        parts.append(f"reason: {params['reason']}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Dispatcher used by the resolver loop (and voice/human APIs)
# ---------------------------------------------------------------------------
async def call_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    out = await _call_tool(ctx, name, args)
    ctx.seen.append(json.dumps(out, default=str)[:4000])  # what the agent legitimately saw (used to vet links it sends)
    return out


async def _call_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    spec = TOOL_SPECS.get(name)
    if not spec and name.startswith("custom_") and ctx.profile:
        from app.profile import call_custom_tool, custom_tool_specs

        cspec = custom_tool_specs(ctx.profile).get(name)
        if not cspec:
            return {"error": f"unknown tool {name}"}
        ids = await customer_identities(ctx)
        who = {"name": ctx.customer.get("display_name"), "verified_phones": sorted(ids["phones"]) if ids["verified"] else [],
               "verified_emails": sorted(ids["emails"]) if ids["verified"] else []}
        return await call_custom_tool(ctx.profile, cspec, args, who)
    if not spec:
        return {"error": f"unknown tool {name}"}
    if spec["kind"] == "action":
        return await propose(ctx, name, args)
    if spec["kind"] in ("sales", "voice"):
        from app import sales

        return await sales.run_tool(ctx, name, args)
    if name == "escalate_to_human":
        ctx.escalation = args
        return {"ok": True, "note": "Escalated. Tell the customer a team member will follow up."}
    shop = await ctx.shop()
    if name == "search_kb":
        hits = await kb.search(ctx.db, [args.get("query", "")])
        for h in hits:
            if h["id"] not in {x["id"] for x in ctx.kb_hits}:
                ctx.kb_hits.append(h)
        refs = {x["id"]: f"S{i + 1}" for i, x in enumerate(ctx.kb_hits)}
        return {"results": [{"ref": refs[h["id"]], "title": h["heading"], "content": h["content"][:1200]} for h in hits]}
    if name == "get_customer_profile":
        ids = await customer_identities(ctx)
        tickets = await ctx.db.all(
            "SELECT number, intent, status, summary, created_at FROM tickets WHERE customer_id=:c AND id<>:t ORDER BY created_at DESC LIMIT 5",
            c=ctx.customer["id"], t=ctx.ticket["id"],
        )
        return {"name": ctx.customer.get("display_name"), "vip": ctx.customer.get("vip"),
                "verified_contacts": bool(ids["phones"] or ids["emails"]),
                "previous_tickets": [{"number": t["number"], "intent": t["intent"], "status": t["status"], "summary": t["summary"]} for t in tickets]}
    if name == "list_customer_orders":
        ids = await customer_identities(ctx)
        if ctx.voice_call_id:
            n = sum(len(await shop.find_orders(phone=p, limit=20)) for p in list(ids["phones"])[:2])
            return {"orders_on_file": n, "note": "Don't read out orders on a call. Ask for the order ID (it's in their order confirmation) "
                                                  "and call verify_order; after it matches, use get_order."}
        if (ctx.conversation or {}).get("channel") == "webchat" and not (ids["phones"] or ids["emails"]):
            return {"orders": [], "note": "This web visitor has not verified a phone yet. If they ask about an order, ask for the order id "
                                          "(website orders look like LL-7KQ3XD) and call get_order, or call request_verification."}
        orders: list[dict] = []
        for p in list(ids["phones"])[:2]:
            orders += await shop.find_orders(phone=p)
        for e in list(ids["emails"])[:2]:
            orders += await shop.find_orders(email=e)
        seen, uniq = set(), []
        for o in orders:
            if o["order_ref"] not in seen:
                seen.add(o["order_ref"])
                uniq.append({k: o[k] for k in ("order_ref", "line_items", "total_inr", "payment_method", "status", "placed_at", "delivered_at", "expected_delivery")})
        return {"orders": uniq[:6], "note": None if uniq else "No orders found for this customer's phone/email. Ask for the order number."}
    if name in ("get_order", "track_shipment"):
        order, belongs = await owned_order(ctx, args.get("order_ref", ""))
        # a website order's id is random (payments.REF_ALPHABET), so quoting it is proof enough to read it; actions still check ownership
        if order and not belongs and not ctx.voice_call_id and "cashfree" in (order.get("tags") or []) and "-" in order["order_ref"]:
            belongs = True
            order = {**order, "customer_phone": None, "customer_email": None, "note": "Opened with the order id the customer gave."}
        if not order or not belongs:
            if ctx.voice_call_id:
                return {"found": False, "note": "Not verified on this call. Ask for the order ID and call verify_order first; don't say whether the order exists."}
            return {"found": False, "note": "No order with that number for this customer. Ask them to re-check the number or the phone/email used."}
        if name == "track_shipment":
            return {k: order[k] for k in ("order_ref", "courier", "awb", "shipment_status", "expected_delivery", "delivered_at", "status")}
        return order
    if name == "verify_order":
        state = await call_state(ctx)
        fails = int(state.get("verify_failures") or 0)
        if fails >= MAX_VERIFY_FAILURES:
            return {"match": False, "note": "Too many order IDs that didn't match on this call. Don't share order details; offer transfer_to_human or a callback."}
        order = await shop.get_order(args.get("order_ref", ""))
        ids = await customer_identities(ctx)
        given = norm_phone(args.get("phone"))
        phone = norm_phone(order.get("customer_phone")) if order else None
        match = bool(phone) and (phone in ids["phones"] or phone == given)
        if not match:
            await _save_call_state(ctx, verify_failures=fails + 1)
            left = MAX_VERIFY_FAILURES - fails - 1
            return {"match": False, "attempts_left": left,
                    "note": "That order ID doesn't match this caller. Don't say whether such an order exists. Ask them to re-check the ID "
                            "or give the mobile number they ordered with." + ("" if left else " No attempts left on this call.")}
        ref = order["order_ref"].upper()
        via = "caller_id" if phone in ids["phones"] else "phone_given"
        await _save_call_state(ctx, verified_orders=sorted(set(state.get("verified_orders", [])) | {ref}))
        if not ctx.voice_call_id:  # chat: the matched phone counts for the rest of this turn
            ids["phones"].add(phone)
        await audit(ctx.db, "ai", "Sahay AI", "order.verified", "order", ref, {"via": via})
        return {"match": True, "order": {k: order[k] for k in ("order_ref", "line_items", "total_inr", "payment_method", "status",
                                                               "shipment_status", "courier", "expected_delivery", "delivered_at", "placed_at")},
                "note": "Verified. You can now help with this order (status, changes) through the usual tools."}
    if name == "check_stock":
        return await shop.check_stock(args.get("sku", ""))
    if name == "search_products":
        items = await shop.search_products(args.get("query", ""))
        ctx.ui.setdefault("products", [])
        skus = [it["sku"] for it in items[:3]]
        slug = await ctx.db.val("SELECT slug FROM tenants WHERE id=:t", t=ctx.db.tenant_id)
        pictured = {r["sku"] for r in await ctx.db.all("SELECT sku FROM commerce_products WHERE sku = ANY(:s) AND image_uri IS NOT NULL", s=skus)}
        for it in items[:3]:
            if it["sku"] not in {p["sku"] for p in ctx.ui["products"]}:
                ctx.ui["products"].append({**{k: it[k] for k in ("sku", "title", "price_inr", "in_stock")},
                                           "image": f"/public/products/{slug}/{it['sku']}.png" if it["sku"] in pictured else None})
        return {"products": items, "note": "Prices and stock are live right now."}
    if name == "get_product":
        p = await shop.get_product(args.get("sku", ""))
        if not p:
            return {"found": False}
        return {k: p[k] for k in ("sku", "title", "description", "category", "price_inr", "stock") if k in p} | {"in_stock": (p.get("stock") or 0) > 0}
    if name == "request_verification":
        ctx.ui["verify_phone"] = True
        return {"ok": True, "note": "A phone verification form is now shown in the chat. Ask them to verify with the OTP so you can see their orders."}
    if name == "analyze_attachment":
        from app.vision import analyze_attachment

        return await analyze_attachment(ctx, args.get("attachment_id", ""), args.get("order_ref", ""))
    if name == "check_policy":
        params = {k: v for k, v in args.items() if k != "action" and v is not None}
        facts, _, _ = await compute_facts(ctx, args.get("action", ""), params)
        g = pol.evaluate(args.get("action", ""), facts, await active_policy(ctx.db))
        return {"decision": g.decision, "reasons": g.reasons, "offer_instead": g.deny_hint}
    return {"error": "not implemented"}

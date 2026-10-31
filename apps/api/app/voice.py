"""Voice agent: the same Agent Profile, skills, tools and policy gate as chat, tuned for phone calls.

A telephony pipeline (our LiveKit + Sarvam worker in voice_agent.py, or Naaradh) calls these functions over HTTP:
  start  → we find the business (dialled number or its key) and the caller (caller ID), preload their
           orders / open tickets / lead status, and return the greeting + system prompt + tools
  turn   → caller said something: we run the agent loop (tools, policy) and return a short line to speak
  tool   → for pipelines that run their own LLM: execute one tool with our policy gate
  end    → save the transcript to the customer's timeline, summarise, set the outcome and next step
The dashboard's "test call" uses the same functions with the browser's speech APIs.
"""

import json
import logging
import time
from typing import Literal

from google.genai import types
from pydantic import BaseModel

from app import llm
from app.config import settings
from app.core import audit, next_ticket_number, norm_phone
from app.db import tenant_session
from app.profile import SKILLS, custom_tool_specs, load_profile, persona_block, tools_for
from app.tools import TOOL_SPECS, ToolContext, call_tool, gemini_tools

log = logging.getLogger("sahay.voice")

VOICE_RULES = """Phone call rules:
- You are speaking, not writing. One or two short sentences per turn. No lists, no markdown, no emojis.
- Speak {language}. Match the caller if they switch language.
- Before a tool call you may say a short filler like "ek second, check kar rahi hoon".
- Repeat numbers back digit by digit to confirm (order numbers, phone numbers, amounts).
- Orders: the caller's own orders (recent_orders, list_customer_orders) were placed with the number they're calling from, so you can
  talk about and help with them directly. For any other order, ask for the order ID and call verify_order (with the mobile number they
  ordered with if they're calling from another phone); continue only if it matches, and never say whether such an order exists before that.
- Never read out links or long codes: use send_link_whatsapp (it sends on Telegram) and tell them to check Telegram.
- If they ask for a person, are very upset, or it's a legal/sensitive matter, use transfer_to_human.
- Let them interrupt; answer the latest thing they said.
{ending}"""
JSON_REPLY = """- When the caller is done, say a warm goodbye and set end_call true.
Reply with ONLY JSON: {"say": "...", "end_call": false}"""
# Streaming pipelines (LiveKit + Sarvam) speak the model's text as it arrives, so no JSON wrapper
STREAM_REPLY = """- Reply with only the words to speak.{script}
- When the caller is done, say a warm goodbye, then call end_call."""
DEVANAGARI = " Write Hindi words in Devanagari and keep English words (order, delivery, refund, numbers) in English, so the voice reads both naturally."

PURPOSE_GOALS = {
    "cod_confirmation": "This is an outbound call to confirm a cash-on-delivery order before it ships. Confirm the order (confirm_cod) or note the cancellation request.",
    "callback": "This is an outbound callback the customer asked for. Topic: {topic}. Help them and capture next steps.",
    "check_in": "This is a short follow-up call after a resolved issue. Ask if everything is fine now.",
    "sales_call": "This is an outbound sales call to a business that isn't a customer yet ({topic}). Say who you are and why you're calling in one "
                  "line, ask if it's a good time, learn what they need, and offer to send details or book a callback. Never push; if they're "
                  "not interested, thank them and end the call.",
}


class VoiceTurn(BaseModel):
    say: str
    end_call: bool = False


class CallSummary(BaseModel):
    summary: str
    intent: str = "other"
    outcome: Literal["resolved", "follow_up", "escalated", "lead_captured", "no_answer", "cod_confirmed", "cancel_requested", "other"] = "other"
    next_step: str = ""
    sentiment: float = 0.0


async def _caller_context(db, customer: dict | None, phone: str | None, exclude_ticket=None) -> dict:
    ctx: dict = {"caller_phone": phone, "known_customer": bool(customer and customer.get("display_name"))}
    if not customer:
        return ctx
    ctx["name"] = customer.get("display_name")
    ctx["vip"] = customer.get("vip")
    if phone:  # the call comes from the number on these orders, so they are the caller's own (other orders need verify_order)
        ctx["orders_on_file"] = await db.val("SELECT count(*) FROM commerce_orders WHERE customer_phone=:p AND status <> 'payment_pending'", p=phone)
        ctx["recent_orders"] = [
            {"order_ref": o["order_ref"], "items": ", ".join(i.get("title", "") for i in (o["line_items"] or [])[:3]), "status": o["status"],
             "shipment_status": o["shipment_status"], "expected_delivery": o["expected_delivery"], "total_inr": o["total_paise"] / 100}
            for o in await db.all("SELECT order_ref, line_items, status, shipment_status, expected_delivery, total_paise FROM commerce_orders "
                                  "WHERE customer_phone=:p AND status <> 'payment_pending' ORDER BY placed_at DESC NULLS LAST LIMIT 3", p=phone)
        ]
    ctx["open_tickets"] = [
        {"number": t["number"], "intent": t["intent"], "summary": t["summary"], "status": t["status"]}
        for t in await db.all("SELECT number, intent, summary, status FROM tickets WHERE customer_id=:c AND status NOT IN ('resolved','closed') "
                              "AND id IS DISTINCT FROM CAST(:x AS uuid) ORDER BY updated_at DESC LIMIT 3", c=customer["id"], x=exclude_ticket)
    ]
    lead = await db.one("SELECT stage, interest, score FROM leads WHERE customer_id=:c ORDER BY created_at DESC LIMIT 1", c=customer["id"])
    if lead:
        ctx["lead"] = lead
    return ctx


async def start_call(tenant_id: str, *, direction: str, from_number: str | None, to_number: str | None,
                     purpose: str = "inbound", call_id: str | None = None, test: bool = False, stream: bool = False) -> dict:
    from app.pipeline import InboundMessage, resolve_customer

    caller = norm_phone(from_number if direction == "inbound" else to_number)
    async with tenant_session(tenant_id) as db:
        prof = await load_profile(db)
        srow = await db.one("SELECT s.*, t.name AS tenant_name FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        customer = await resolve_customer(db, InboundMessage(channel="voice", from_phone=caller, verified=True)) if caller else None
        if customer is None:
            customer = await resolve_customer(db, InboundMessage(channel="voice", visitor_id=f"call-{time.time_ns()}"))
        existing = await db.one("SELECT * FROM voice_calls WHERE id=:id", id=call_id) if call_id else None
        if existing and existing["conversation_id"]:  # already started (e.g. an outbound call the provider is dialing): reuse it
            conv = await db.one("SELECT * FROM conversations WHERE id=:c", c=existing["conversation_id"])
            ticket = await db.one("SELECT * FROM tickets WHERE id=:t", t=existing["ticket_id"])
        else:
            conv = await db.one("INSERT INTO conversations (tenant_id, customer_id, channel, is_simulation) VALUES (:t,:c,'voice',:s) RETURNING *",
                                t=tenant_id, c=customer["id"], s=test)
            ticket = await db.one(
                "INSERT INTO tickets (tenant_id, number, customer_id, subject, status, is_simulation, sla_due_at) VALUES (:t,:n,:c,:s,'open',:sim, now() + interval '1 hour') RETURNING *",
                t=tenant_id, n=await next_ticket_number(db), c=customer["id"], s=f"Phone call ({purpose})", sim=test)
        context = await _caller_context(db, customer, caller, exclude_ticket=ticket["id"])
        goal_ctx = (existing or {}).get("context") or {}
        if existing:
            call = await db.one("UPDATE voice_calls SET status='in_progress', started_at=now(), conversation_id=:cv, ticket_id=:tk, customer_id=:c, "
                                "context = context || :x WHERE id=:id RETURNING *", cv=conv["id"], tk=ticket["id"], c=customer["id"], x=context, id=call_id)
        else:
            # a sales lead who hasn't bought yet calling in: the call belongs on Sales → Calls, not with customer support
            lead = await db.val("SELECT id FROM leads WHERE customer_id=:c AND stage NOT IN ('customer','dropped') ORDER BY created_at DESC LIMIT 1",
                                c=customer["id"])
            call = await db.one(
                "INSERT INTO voice_calls (tenant_id, direction, purpose, from_number, to_number, customer_id, lead_id, ticket_id, conversation_id, status, context, started_at) "
                "VALUES (:t,:d,:p,:f,:to,:c,:l,:tk,:cv,'in_progress',:x, now()) RETURNING *",
                t=tenant_id, d=direction, p=purpose, f=norm_phone(from_number), to=norm_phone(to_number), c=customer["id"], l=lead, tk=ticket["id"],
                cv=conv["id"], x={**context, "test": test})
        v = prof["voice"]
        name = (context.get("name") or "").split(" ")[0]
        greeting = v.get("greeting") or (
            f"Namaste{' ' + name + ' ji' if name else ''}! {srow['tenant_name']} se {prof['agent_name']} baat kar rahi hoon. Bataiye, main aapki kya madad kar sakti hoon?"
            if direction == "inbound" else
            f"Namaste{' ' + name + ' ji' if name else ''}, main {srow['tenant_name']} se {prof['agent_name']} bol rahi hoon.")
        await db.exec("UPDATE voice_calls SET transcript=:tr WHERE id=:id", tr=[{"role": "agent", "text": greeting, "at": time.time()}], id=call["id"])
    system = build_system(prof, srow, context, purpose, goal_ctx, stream=stream)
    names = _voice_tools(prof)
    specs = {**TOOL_SPECS, **custom_tool_specs(prof)}
    return {"call_id": str(call["id"]), "greeting": greeting, "context": context, "system_prompt": system,
            "tools": [{"name": n, "description": specs[n]["description"], "parameters": specs[n]["parameters"]} for n in names if n in specs]}


def build_system(prof: dict, srow: dict, context: dict, purpose: str, goal_ctx: dict, stream: bool = False) -> str:
    lang = {"hinglish": "Hinglish (Hindi + English, natural Indian phone style)", "hi": "simple Hindi", "en": "Indian English"}.get(
        prof["voice"].get("language") or "hinglish", "Hinglish")
    goal = PURPOSE_GOALS.get(purpose, "").format(topic=goal_ctx.get("topic", ""))
    return (f"{persona_block(prof, srow)} {srow['business_description']}\nBrand voice: {srow['brand_voice']}\n"
            f"Business hours: {prof['voice'].get('business_hours')}\n{goal}\n"
            f"What we already know about the caller (looked up before the call): {json.dumps(context, default=str)}\n"
            "recent_orders are this caller's own latest orders (placed with the number they're calling from). Use tools for orders, stock, "
            "price and actions. Policy rules are enforced by the tools; never promise what a tool didn't confirm.\n"
            + VOICE_RULES.format(language=lang, ending=STREAM_REPLY.format(script="" if prof["voice"].get("language") == "en" else DEVANAGARI)
                                 if stream else JSON_REPLY))


def _voice_tools(prof: dict) -> list[str]:
    skills = [k for k, on in prof["skills"].items() if on and k in SKILLS]
    return tools_for(skills, prof, "voice", True)


async def turn(tenant_id: str, call_id: str, text: str) -> dict:
    t0 = time.perf_counter()
    async with tenant_session(tenant_id) as db:
        call = await db.one("SELECT * FROM voice_calls WHERE id=:id", id=call_id)
        if not call or call["status"] != "in_progress":
            raise ValueError("call is not in progress")
        prof = await load_profile(db)
        srow = await db.one("SELECT s.*, t.name AS tenant_name FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        customer = await db.one("SELECT * FROM customers WHERE id=:c", c=call["customer_id"])
        ticket = await db.one("SELECT * FROM tickets WHERE id=:t", t=call["ticket_id"])
        conv = await db.one("SELECT * FROM conversations WHERE id=:c", c=call["conversation_id"])
        transcript = list(call["transcript"] or []) + [{"role": "caller", "text": text, "at": time.time()}]
        ctx = ToolContext(db=db, tenant_id=tenant_id, ticket=ticket, customer=customer, conversation=conv, profile=prof,
                          voice_call_id=call_id)
        names = _voice_tools(prof)
        decl = gemini_tools(names, custom_tool_specs(prof))
        system = build_system(prof, srow, call["context"] or {}, call["purpose"], call["context"] or {})
        contents = [types.Content(role="user" if m["role"] == "caller" else "model", parts=[types.Part.from_text(text=m["text"])])
                    for m in transcript[-16:]]
        if contents and contents[0].role == "model":
            contents.insert(0, types.Content(role="user", parts=[types.Part.from_text(text="(call connected)")]))
        used = []
        reply = VoiceTurn(say="Maaf kijiye, ek second... kya aap dobara bata sakte hain?")
        for _ in range(5):
            resp = await llm.generate(model=settings.model_voice, contents=contents, system=system, tools=decl, temperature=0.3,
                                      purpose="voice", tenant_id=tenant_id, thinking="minimal")
            calls = resp.function_calls or []
            cand = resp.candidates[0] if resp.candidates else None
            if not cand or not calls:
                raw = resp.text or ""
                try:
                    s, e = raw.find("{"), raw.rfind("}")
                    reply = VoiceTurn.model_validate(json.loads(raw[s:e + 1]))
                except Exception:
                    reply = VoiceTurn(say=raw.strip()[:300] or reply.say)
                break
            contents.append(cand.content)
            parts = []
            for fc in calls:
                args = dict(fc.args or {})
                try:
                    out = await call_tool(ctx, fc.name, args) if fc.name in names else {"error": "not available on calls"}
                except Exception as e:
                    out = {"error": str(e)[:200]}
                used.append({"tool": fc.name, "args": args, "result": json.loads(json.dumps(out, default=str))})
                parts.append(types.Part.from_function_response(name=fc.name, response={"result": json.loads(json.dumps(out, default=str))}))
            contents.append(types.Content(role="user", parts=parts))
        transcript.append({"role": "agent", "text": reply.say, "at": time.time(), "tools": [u["tool"] for u in used]})
        await db.exec("UPDATE voice_calls SET transcript=:tr WHERE id=:id", tr=transcript, id=call_id)
    return {"say": reply.say, "end_call": reply.end_call or bool(ctx.ui.get("transfer")), "transfer_to": (prof["voice"].get("transfer_number") or None) if ctx.ui.get("transfer") else None,
            "whatsapp_sent": ctx.ui.get("whatsapp_sent") or [], "actions": [a for a in ctx.actions], "tools_used": used,
            "latency_ms": int((time.perf_counter() - t0) * 1000)}


async def run_tool(tenant_id: str, call_id: str, name: str, args: dict) -> dict:
    async with tenant_session(tenant_id) as db:
        call = await db.one("SELECT * FROM voice_calls WHERE id=:id", id=call_id)
        prof = await load_profile(db)
        ctx = ToolContext(db=db, tenant_id=tenant_id, ticket=await db.one("SELECT * FROM tickets WHERE id=:t", t=call["ticket_id"]),
                          customer=await db.one("SELECT * FROM customers WHERE id=:c", c=call["customer_id"]),
                          conversation=await db.one("SELECT * FROM conversations WHERE id=:c", c=call["conversation_id"]), profile=prof,
                          voice_call_id=call_id)
        if name not in _voice_tools(prof):
            return {"error": f"{name} is not enabled for calls"}
        return await call_tool(ctx, name, args)


async def end_call(tenant_id: str, call_id: str, transcript: list[dict] | None = None, status: str = "completed") -> dict:
    async with tenant_session(tenant_id) as db:
        call = await db.one("SELECT * FROM voice_calls WHERE id=:id", id=call_id)
        if not call:
            raise ValueError("unknown call")
        tr = transcript or call["transcript"] or []
    convo = "\n".join(f"{'Caller' if m['role'] == 'caller' else 'Agent'}: {m['text']}" for m in tr)
    summary = CallSummary(summary="No conversation.", outcome="no_answer") if len(tr) <= 1 else await llm.structured(
        CallSummary, model=settings.model_triage, purpose="call_summary", tenant_id=tenant_id, thinking=settings.thinking_triage or None,
        system="Summarise this support/sales phone call for the business owner in 1-2 lines, pick the outcome, the main intent "
               "(order_status, damaged_item, cancel_order, pre_sales, bulk_order, callback_request, cod_query, complaint_general, other) "
               "and the next step for the team (empty if none). sentiment -1..1.",
        contents=convo)
    async with tenant_session(tenant_id) as db:
        for m in tr:  # the call lands on the customer's single timeline
            await db.exec(
                "INSERT INTO messages (tenant_id, conversation_id, ticket_id, customer_id, direction, channel, author_type, author_name, text, text_redacted, status, triage) "
                "VALUES (:t,:c,:tk,:cu,:d,'voice',:a,:n,:tx,:tx,'sent',:tr)",
                t=tenant_id, c=call["conversation_id"], tk=call["ticket_id"], cu=call["customer_id"], d="in" if m["role"] == "caller" else "out",
                a="customer" if m["role"] == "caller" else "ai", n=None if m["role"] == "caller" else "Voice agent", tx=m["text"],
                tr={"voice": True} if m["role"] == "caller" else None)
        ticket_status = {"resolved": "resolved", "cod_confirmed": "resolved", "escalated": "escalated", "no_answer": "closed"}.get(summary.outcome, "pending_customer")
        await db.exec(
            "UPDATE tickets SET intent=:i, summary=:s, sentiment=:se, status=:st, resolved_at=CASE WHEN :st='resolved' THEN now() END, "
            "resolution_type=CASE WHEN :st='resolved' THEN 'ai_auto' END, first_response_at=coalesce(first_response_at, now()), updated_at=now() WHERE id=:id",
            i=summary.intent, s=summary.summary, se=summary.sentiment, st=ticket_status, id=call["ticket_id"])
        if summary.outcome == "escalated" or summary.next_step:
            from app.approvals import create_approval

            if summary.outcome == "escalated":
                await create_approval(db, kind="handoff", ticket_id=call["ticket_id"], summary=f"Call: {summary.summary}",
                                      suggestion=summary.next_step or "Call the customer back", dry_run=bool((call["context"] or {}).get("test")))
        lead = await db.one("SELECT id FROM leads WHERE customer_id=:c ORDER BY created_at DESC LIMIT 1", c=call["customer_id"])
        if lead:
            from app.sales import add_event

            await add_event(db, lead["id"], "call", f"{call['direction'].title()} call: {summary.summary}", {"call_id": call_id, "outcome": summary.outcome})
        await db.exec("UPDATE voice_calls SET status=:st, ended_at=now(), transcript=:tr, summary=:s, outcome=:o, next_step=:n WHERE id=:id",
                      st=status, tr=tr, s=summary.summary, o=summary.outcome, n=summary.next_step, id=call_id)
        await audit(db, "ai", "Voice agent", "call.ended", "voice_call", call_id, {"outcome": summary.outcome})
        if not (call["context"] or {}).get("test"):
            from app.hooks_out import emit, emit_ticket

            await emit(db, "call.completed", {"call_id": call_id, "direction": call["direction"], "purpose": call["purpose"],
                                              "summary": summary.summary, "outcome": summary.outcome, "next_step": summary.next_step,
                                              "duration_turns": len(tr)})
            if ticket_status in ("escalated", "resolved"):
                await emit_ticket(db, f"ticket.{ticket_status}", call["ticket_id"])
    return summary.model_dump()

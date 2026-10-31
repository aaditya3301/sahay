"""Dashboard API (authenticated, tenant-scoped through RLS)."""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import insights, kb, policy as pol
from app.approvals import decide
from app.channels import owner_message_preview, send_to_customer
from app.config import settings
from app.core import Ctx, CtxDep, audit, enqueue, jsonable
from app.db import system_session, tenant_session
from app.pipeline import InboundAttachment, InboundMessage, ingest, send_draft
from app.tools import ToolContext, propose

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")


@router.get("/me")
async def me(ctx: Ctx = CtxDep):
    async with system_session() as db:
        u = await db.one("SELECT id, name, email FROM users WHERE id=:u", u=ctx.user_id)
        tenants = await db.all(
            "SELECT t.id, t.slug, t.name, m.role FROM tenant_members m JOIN tenants t ON t.id=m.tenant_id WHERE m.user_id=:u ORDER BY t.name",
            u=ctx.user_id,
        )
        key = await db.val("SELECT external_key FROM channel_routes WHERE channel='webchat' AND tenant_id=:t LIMIT 1", t=ctx.tenant_id)
    async with tenant_session(ctx.tenant_id) as db:
        prefix = await db.val("SELECT ticket_prefix FROM tenant_settings")
        setup_done = await db.val("SELECT setup_done FROM tenant_settings")
    return {"user": u, "tenants": tenants, "tenant_id": ctx.tenant_id, "widget_key": key, "ticket_prefix": prefix or "T", "setup_done": setup_done is not False}


@router.get("/overview")
async def overview(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return await db.one(
            "SELECT (SELECT count(*) FROM tickets WHERE NOT is_simulation AND status NOT IN ('resolved','closed')) AS open_tickets, "
            "(SELECT count(*) FROM approvals a JOIN tickets t ON t.id=a.ticket_id WHERE a.status='pending' AND NOT t.is_simulation) AS pending_approvals, "
            "(SELECT count(*) FROM security_events WHERE created_at > now()-interval '24 hours') AS security_24h, "
            "(SELECT count(*) FROM kb_gap_suggestions WHERE status='suggested') AS gap_suggestions"
        )


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
@router.get("/tickets")
async def tickets(ctx: Ctx = CtxDep, status: str = "active", q: str = "", sim: bool = False):
    where = {"active": "t.status NOT IN ('resolved','closed')", "resolved": "t.status IN ('resolved','closed')",
             "escalated": "t.status='escalated'", "pending_approval": "t.status='pending_approval'", "all": "true"}.get(status, "true")
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all(
            f"SELECT t.id, t.number, t.subject, t.status, t.intent, t.urgency, t.sentiment, t.frustration_score, t.priority_score, "
            f"t.sensitive, t.injection_suspected, t.language, t.order_refs, t.resolution_type, t.summary, t.created_at, t.updated_at, "
            f"c.display_name AS customer_name, c.vip, "
            f"(SELECT m.channel FROM messages m WHERE m.ticket_id=t.id AND m.direction='in' ORDER BY m.created_at DESC LIMIT 1) AS channel, "
            f"(SELECT coalesce(m.text, m.transcript) FROM messages m WHERE m.ticket_id=t.id ORDER BY m.created_at DESC LIMIT 1) AS last_message, "
            f"(SELECT count(*) FROM approvals a WHERE a.ticket_id=t.id AND a.status='pending') AS pending "
            f"FROM tickets t JOIN customers c ON c.id=t.customer_id WHERE {where} AND t.is_simulation=:sim "
            f"AND (:q = '' OR t.summary ILIKE '%' || :q || '%' OR c.display_name ILIKE '%' || :q || '%' OR CAST(t.number AS text)=:q) "
            f"ORDER BY CASE WHEN t.status IN ('resolved','closed') THEN 1 ELSE 0 END, t.priority_score DESC, t.updated_at DESC LIMIT 200",
            q=q, sim=sim,
        )
    return jsonable(rows)


@router.get("/tickets/{tid}")
async def ticket(tid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        t = await db.one("SELECT * FROM tickets WHERE id=:id", id=tid)
        if not t:
            raise HTTPException(404)
        t.pop("complaint_embedding", None)
        customer = await db.one("SELECT * FROM customers WHERE id=:c", c=t["customer_id"])
        identities = await db.all("SELECT kind, value, verified FROM customer_identities WHERE customer_id=:c", c=t["customer_id"])
        messages = await db.all(
            "SELECT m.*, (SELECT json_agg(json_build_object('id', a.id, 'kind', a.kind, 'mime', a.mime, 'analysis', a.analysis, 'screening', a.screening, 'filename', a.filename)) "
            "FROM attachments a WHERE a.message_id=m.id) AS attachments FROM messages m WHERE m.ticket_id=:t ORDER BY m.created_at", t=tid,
        )
        runs = await db.all("SELECT * FROM agent_runs WHERE ticket_id=:t ORDER BY created_at", t=tid)
        steps = await db.all(
            "SELECT s.* FROM agent_steps s JOIN agent_runs r ON r.id=s.run_id WHERE r.ticket_id=:t ORDER BY s.created_at, s.seq", t=tid
        )
        for r in runs:
            r["steps"] = [s for s in steps if s["run_id"] == r["id"]]
        actions = await db.all("SELECT * FROM actions WHERE ticket_id=:t ORDER BY created_at", t=tid)
        drafts = await db.all("SELECT * FROM drafts WHERE ticket_id=:t ORDER BY created_at", t=tid)
        approvals = await db.all("SELECT * FROM approvals WHERE ticket_id=:t ORDER BY created_at", t=tid)
        fraud = await db.all("SELECT * FROM fraud_signals WHERE ticket_id=:t", t=tid)
        security = await db.all("SELECT * FROM security_events WHERE ticket_id=:t ORDER BY created_at", t=tid)
        other = await db.all(
            "SELECT id, number, status, intent, summary, created_at FROM tickets WHERE customer_id=:c AND id<>:t ORDER BY created_at DESC LIMIT 10",
            c=t["customer_id"], t=tid,
        )
        channels = await db.all("SELECT DISTINCT channel FROM conversations WHERE customer_id=:c", c=t["customer_id"])
    return jsonable({"ticket": t, "customer": customer, "identities": identities, "messages": messages, "runs": runs,
                     "actions": actions, "drafts": drafts, "approvals": approvals, "fraud_signals": fraud,
                     "security_events": security, "other_tickets": other, "channels": [c["channel"] for c in channels]})


class Reply(BaseModel):
    text: str


@router.post("/tickets/{tid}/reply")
async def reply(tid: str, body: Reply, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        conv = await db.one(
            "SELECT c.* FROM conversations c JOIN messages m ON m.conversation_id=c.id WHERE m.ticket_id=:t AND m.direction='in' "
            "ORDER BY m.created_at DESC LIMIT 1", t=tid,
        )
        msg = await send_to_customer(db, conv, body.text, tid, "human", ctx.user_name)
        await db.exec("UPDATE tickets SET status='pending_customer', resolution_type=coalesce(resolution_type,'human') WHERE id=:t AND status<>'resolved'", t=tid)
        await audit(db, "human", ctx.user_name, "ticket.replied", "ticket", tid)
    return jsonable(msg)


class StatusBody(BaseModel):
    status: str


@router.post("/tickets/{tid}/status")
async def set_status(tid: str, body: StatusBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec(
            "UPDATE tickets SET status=:s, resolved_at=CASE WHEN :s IN ('resolved','closed') THEN now() ELSE resolved_at END, "
            "resolution_type=CASE WHEN :s='resolved' THEN coalesce(resolution_type,'human') ELSE resolution_type END, updated_at=now() WHERE id=:t",
            s=body.status, t=tid,
        )
        await audit(db, "human", ctx.user_name, "ticket.status", "ticket", tid, {"status": body.status})
        if body.status in ("escalated", "resolved"):
            from app.hooks_out import emit_ticket

            await emit_ticket(db, f"ticket.{body.status}", tid)
    return {"ok": True}


class HumanAction(BaseModel):
    type: str
    params: dict


@router.post("/tickets/{tid}/actions")
async def human_action(tid: str, body: HumanAction, ctx: Ctx = CtxDep):
    """A human proposing an action goes through the same gate as the AI."""
    async with tenant_session(ctx.tenant_id) as db:
        t = await db.one("SELECT * FROM tickets WHERE id=:id", id=tid)
        c = await db.one("SELECT * FROM customers WHERE id=:c", c=t["customer_id"])
        tc = ToolContext(db=db, tenant_id=ctx.tenant_id, ticket=t, customer=c, actor="human", actor_name=ctx.user_name)
        return jsonable(await propose(tc, body.type, body.params))


# ---------------------------------------------------------------------------
# Drafts + approvals
# ---------------------------------------------------------------------------
class DraftSend(BaseModel):
    text: str | None = None


@router.post("/drafts/{did}/send")
async def draft_send(did: str, body: DraftSend, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await send_draft(db, did, ctx.user_name, body.text))


@router.post("/drafts/{did}/discard")
async def draft_discard(did: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE drafts SET status='discarded' WHERE id=:id", id=did)
        await db.exec("UPDATE approvals SET status='rejected', decided_by=:b, decided_at=now() WHERE draft_id=:id AND status='pending'", b=ctx.user_name, id=did)
    return {"ok": True}


@router.get("/approvals")
async def approvals(ctx: Ctx = CtxDep, status: str = "pending"):
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all(
            "SELECT a.*, t.number, t.intent, t.urgency, t.frustration_score, c.display_name AS customer_name, "
            "ac.type AS action_type, ac.params AS action_params, ac.value_paise, ac.facts, ac.decision_reason, d.text AS draft_text "
            "FROM approvals a JOIN tickets t ON t.id=a.ticket_id JOIN customers c ON c.id=t.customer_id "
            "LEFT JOIN actions ac ON ac.id=a.action_id LEFT JOIN drafts d ON d.id=a.draft_id "
            "WHERE a.status=:s AND NOT t.is_simulation ORDER BY a.created_at DESC LIMIT 200", s=status,
        )
    return jsonable(rows)


class Decision(BaseModel):
    decision: str  # approve | reject
    text: str | None = None


@router.post("/approvals/{aid}/decide")
async def approval_decide(aid: str, body: Decision, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await decide(db, aid, body.decision, ctx.user_name, "dashboard", body.text))


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
@router.get("/customers")
async def customers(ctx: Ctx = CtxDep, q: str = ""):
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all(
            "SELECT c.*, (SELECT count(*) FROM tickets t WHERE t.customer_id=c.id) AS tickets, "
            "(SELECT max(created_at) FROM messages m WHERE m.customer_id=c.id) AS last_contact "
            "FROM customers c WHERE NOT c.is_simulation AND (:q='' OR c.display_name ILIKE '%'||:q||'%' OR c.primary_phone ILIKE '%'||:q||'%' "
            "OR c.primary_email ILIKE '%'||:q||'%') ORDER BY last_contact DESC NULLS LAST LIMIT 200", q=q,
        )
    return jsonable(rows)


@router.get("/customers/{cid}")
async def customer(cid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        c = await db.one("SELECT * FROM customers WHERE id=:c", c=cid)
        ids = await db.all("SELECT kind, value, verified FROM customer_identities WHERE customer_id=:c", c=cid)
        timeline = await db.all(
            "SELECT 'message' AS kind, m.created_at AS at, m.channel, m.direction, coalesce(m.text, m.transcript) AS text, t.number AS ticket "
            "FROM messages m LEFT JOIN tickets t ON t.id=m.ticket_id WHERE m.customer_id=:c "
            "UNION ALL SELECT 'action', a.created_at, NULL, NULL, a.type || ' → ' || a.status, t.number FROM actions a JOIN tickets t ON t.id=a.ticket_id WHERE a.customer_id=:c "
            "UNION ALL SELECT 'ticket', t.created_at, NULL, NULL, 'Ticket opened: ' || coalesce(t.summary,''), t.number FROM tickets t WHERE t.customer_id=:c "
            "ORDER BY at DESC LIMIT 300", c=cid,
        )
        tickets_ = await db.all("SELECT id, number, status, intent, summary, created_at FROM tickets WHERE customer_id=:c ORDER BY created_at DESC", c=cid)
        fraud = await db.all("SELECT kind, score, created_at FROM fraud_signals WHERE customer_id=:c ORDER BY created_at DESC", c=cid)
        from app.commerce import get_commerce

        shop = await get_commerce(db)
        orders = []
        for i in ids:
            if i["kind"] == "phone":
                orders += await shop.find_orders(phone=i["value"], limit=10)
            elif i["kind"] == "email":
                orders += await shop.find_orders(email=i["value"], limit=10)
        seen = set()
        orders = [o for o in orders if not (o["order_ref"] in seen or seen.add(o["order_ref"]))]
    return jsonable({"customer": c, "identities": ids, "timeline": timeline, "tickets": tickets_, "fraud_signals": fraud, "orders": orders})


# ---------------------------------------------------------------------------
# Knowledge base + gaps
# ---------------------------------------------------------------------------
@router.get("/kb/documents")
async def kb_docs(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all(
            "SELECT d.id, d.title, d.source, d.url, d.created_at, length(d.content_md) AS chars, "
            "(SELECT count(*) FROM kb_chunks c WHERE c.document_id=d.id) AS chunks FROM kb_documents d WHERE d.active ORDER BY d.created_at DESC"))


@router.get("/kb/documents/{did}")
async def kb_doc(did: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.one("SELECT * FROM kb_documents WHERE id=:id", id=did))


class DocBody(BaseModel):
    title: str
    content: str


@router.post("/kb/documents")
async def kb_add(body: DocBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return {"id": await kb.add_document(db, body.title, body.content, "manual")}


@router.delete("/kb/documents/{did}")
async def kb_delete(did: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE kb_documents SET active=false WHERE id=:id", id=did)
    return {"ok": True}


@router.post("/kb/upload")
async def kb_upload(file: UploadFile = File(...), ctx: Ctx = CtxDep):
    data = await file.read()
    text = kb.extract_text(file.filename or "file.txt", data)
    if not text.strip():
        raise HTTPException(400, "Could not read any text from this file")
    async with tenant_session(ctx.tenant_id) as db:
        return {"id": await kb.add_document(db, (file.filename or "Upload").rsplit(".", 1)[0], text, "upload")}


class CrawlBody(BaseModel):
    url: str


@router.post("/kb/crawl")
async def kb_crawl(body: CrawlBody, ctx: Ctx = CtxDep):
    try:
        async with tenant_session(ctx.tenant_id) as db:
            ids = await kb.crawl_site(db, body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ids:
        raise HTTPException(422, "We couldn't read any pages from that website. Check the address, or add your policies with Write article or Upload PDF / text.")
    return {"documents": len(ids)}


class SearchBody(BaseModel):
    query: str


@router.post("/kb/search")
async def kb_search(body: SearchBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return await kb.search(db, [body.query])


@router.get("/kb/gaps")
async def kb_gaps(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        sugg = await db.all("SELECT * FROM kb_gap_suggestions WHERE status='suggested' ORDER BY created_at DESC")
        open_gaps = await db.all("SELECT id, question, created_at FROM kb_gaps WHERE suggestion_id IS NULL ORDER BY created_at DESC LIMIT 50")
    return jsonable({"suggestions": sugg, "open_questions": open_gaps})


@router.post("/kb/gaps/build")
async def kb_gaps_build(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        docs = await db.val("SELECT count(*) FROM kb_documents WHERE active")
        from_chats = await insights.build_gap_suggestions(db, ctx.tenant_id)
        from_scan = 0
        if not from_chats and docs:  # no repeated customer questions yet: check the knowledge itself
            try:
                from_scan = await insights.scan_knowledge_for_gaps(db, ctx.tenant_id)
            except Exception:
                log.exception("knowledge scan failed")
                raise HTTPException(502, "Couldn't check your knowledge right now. Please try again in a minute.")
        open_questions = await db.val("SELECT count(*) FROM kb_gaps WHERE suggestion_id IS NULL")
    return {"created": from_chats + from_scan, "from_chats": from_chats, "from_scan": from_scan, "documents": docs, "open_questions": open_questions}


class GapDecision(BaseModel):
    decision: str
    title: str | None = None
    article_md: str | None = None


@router.post("/kb/gaps/{sid}/decide")
async def kb_gap_decide(sid: str, body: GapDecision, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        s = await db.one("SELECT * FROM kb_gap_suggestions WHERE id=:id", id=sid)
        if body.decision == "approve":
            await insights.approve_gap(db, sid, body.title or s["draft_title"], body.article_md or s["draft_article_md"])
        else:
            await db.exec("UPDATE kb_gap_suggestions SET status='rejected' WHERE id=:id", id=sid)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------
def _policy_view(row: dict) -> dict:
    doc = pol.PolicyDoc.model_validate(row["rules"])
    return {**row, "cards": [pol.render_rule(r) for r in doc.rules], "problems": pol.validate(doc)}


@router.get("/policies")
async def policies(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all("SELECT * FROM policies ORDER BY version DESC LIMIT 20")
    return jsonable({"versions": [_policy_view(r) for r in rows], "facts": pol.FACTS, "actions": pol.ACTIONS})


class CompileBody(BaseModel):
    text: str


@router.post("/policies/compile")
async def policy_compile(body: CompileBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        biz = await db.val("SELECT business_description FROM tenant_settings")
    res = await pol.compile_policy(body.text, ctx.tenant_id, biz or "")
    async with tenant_session(ctx.tenant_id) as db:
        v = (await db.val("SELECT max(version) FROM policies") or 0) + 1
        row = await db.one(
            "INSERT INTO policies (tenant_id, version, status, source_text, rules, questions) VALUES (:t,:v,'draft',:s,:r,:q) RETURNING *",
            t=ctx.tenant_id, v=v, s=body.text, r=res.policy.model_dump(), q=res.questions,
        )
    return jsonable(_policy_view(row))


class RulesBody(BaseModel):
    rules: dict


@router.put("/policies/{pid}")
async def policy_save(pid: str, body: RulesBody, ctx: Ctx = CtxDep):
    doc = pol.PolicyDoc.model_validate(body.rules)
    problems = pol.validate(doc)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    async with tenant_session(ctx.tenant_id) as db:
        row = await db.one("UPDATE policies SET rules=:r WHERE id=:id AND status='draft' RETURNING *", r=doc.model_dump(), id=pid)
    if not row:
        raise HTTPException(400, "Only draft versions can be edited")
    return jsonable(_policy_view(row))


class TestBody(BaseModel):
    action: str
    facts: dict


@router.post("/policies/{pid}/test")
async def policy_test(pid: str, body: TestBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        row = await db.one("SELECT rules FROM policies WHERE id=:id", id=pid)
    return pol.evaluate(body.action, body.facts, pol.PolicyDoc.model_validate(row["rules"])).model_dump()


@router.post("/policies/{pid}/activate")
async def policy_activate(pid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE policies SET status='archived' WHERE status='active'")
        await db.exec("UPDATE policies SET status='active', confirmed_at=now() WHERE id=:id", id=pid)
        await audit(db, "human", ctx.user_name, "policy.activated", "policy", pid)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------
@router.get("/sims/personas")
async def personas():
    from app.sims import PERSONAS

    return PERSONAS


@router.get("/sims")
async def sims(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all(
            "SELECT r.*, (SELECT count(*) FROM sim_cases c WHERE c.run_id=r.id) AS cases, "
            "(SELECT count(*) FROM sim_cases c WHERE c.run_id=r.id AND c.status IN ('done','error')) AS finished "
            "FROM sim_runs r ORDER BY created_at DESC LIMIT 30"))


@router.get("/sims/{rid}")
async def sim(rid: str, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        run = await db.one("SELECT * FROM sim_runs WHERE id=:id", id=rid)
        cases = await db.all("SELECT * FROM sim_cases WHERE run_id=:id ORDER BY created_at", id=rid)
    return jsonable({"run": run, "cases": cases})


class MysteryBody(BaseModel):
    personas: list[str] = []
    max_turns: int = 3


@router.post("/sims/mystery")
async def sim_mystery(body: MysteryBody, ctx: Ctx = CtxDep):
    from app.sims import run_mystery_shoppers

    async with tenant_session(ctx.tenant_id) as db:
        run = await db.one("INSERT INTO sim_runs (tenant_id, kind, config) VALUES (:t,'mystery_shopper',:c) RETURNING id",
                           t=ctx.tenant_id, c=body.model_dump())
    enqueue(run_mystery_shoppers, ctx.tenant_id, str(run["id"]), body.personas or None, body.max_turns, name="mystery")
    return {"id": run["id"]}


@router.post("/sims/shadow")
async def sim_shadow(file: UploadFile = File(...), limit: int = Form(25), ctx: Ctx = CtxDep):
    from app.sims import parse_history_csv, run_shadow

    rows = parse_history_csv(await file.read())[:limit]
    if not rows:
        raise HTTPException(400, "CSV needs a customer_message column (optional: human_reply, outcome, order_ref)")
    async with tenant_session(ctx.tenant_id) as db:
        run = await db.one("INSERT INTO sim_runs (tenant_id, kind, config) VALUES (:t,'shadow',:c) RETURNING id",
                           t=ctx.tenant_id, c={"rows": len(rows), "file": file.filename})
    enqueue(run_shadow, ctx.tenant_id, str(run["id"]), rows, name="shadow")
    return {"id": run["id"], "rows": len(rows)}


# ---------------------------------------------------------------------------
# Insights + learning
# ---------------------------------------------------------------------------
@router.get("/insights/metrics")
async def ins_metrics(ctx: Ctx = CtxDep, days: int = 14):
    end = date.today()
    async with tenant_session(ctx.tenant_id) as db:
        m = await insights.metrics(db, end - timedelta(days=days - 1), end)
        at_risk = await db.all(
            "SELECT t.id, t.number, t.summary, t.frustration_score, t.status, c.display_name FROM tickets t JOIN customers c ON c.id=t.customer_id "
            "WHERE NOT t.is_simulation AND t.status NOT IN ('resolved','closed') AND t.frustration_score >= 60 ORDER BY t.frustration_score DESC LIMIT 10")
    return jsonable({**m, "at_risk": at_risk})


@router.get("/insights/reports")
async def ins_reports(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all("SELECT * FROM insights_reports ORDER BY created_at DESC LIMIT 12"))


@router.post("/insights/reports")
async def ins_generate(ctx: Ctx = CtxDep, days: int = 7):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await insights.weekly_report(db, ctx.tenant_id, days=days))


@router.get("/learning")
async def learning(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        lessons = await db.all("SELECT * FROM playbook_lessons ORDER BY created_at DESC LIMIT 50")
        edits = await db.all("SELECT intent, original_text, final_text, edit_ratio, created_at FROM human_edits WHERE edit_ratio > 0 ORDER BY created_at DESC LIMIT 20")
        sugg = await insights.autonomy_suggestions(db)
        s = await db.one("SELECT autonomy, default_autonomy, intents FROM tenant_settings")
    from app.pipeline import DEFAULT_INTENTS

    return jsonable({"lessons": lessons, "edits": edits, "autonomy_suggestions": sugg, "autonomy": s["autonomy"],
                     "default_autonomy": s["default_autonomy"], "intents": s["intents"] or DEFAULT_INTENTS})


@router.post("/learning/extract")
async def learning_extract(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return {"lessons": await insights.extract_lessons(db, ctx.tenant_id)}


class LessonDecision(BaseModel):
    decision: str
    lesson: str | None = None


@router.post("/learning/lessons/{lid}")
async def lesson_decide(lid: str, body: LessonDecision, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE playbook_lessons SET status=:s, lesson=coalesce(:l, lesson) WHERE id=:id",
                      s="approved" if body.decision == "approve" else "rejected", l=body.lesson, id=lid)
    return {"ok": True}


class AutonomyBody(BaseModel):
    intent: str
    mode: str  # auto | draft | human


@router.post("/settings/autonomy")
async def set_autonomy(body: AutonomyBody, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE tenant_settings SET autonomy = autonomy || jsonb_build_object(CAST(:i AS text), CAST(:m AS text))", i=body.intent, m=body.mode)
        await audit(db, "human", ctx.user_name, "autonomy.changed", "intent", body.intent, {"mode": body.mode})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Security + audit
# ---------------------------------------------------------------------------
@router.get("/security")
async def security(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        events = await db.all(
            "SELECT e.*, t.number FROM security_events e LEFT JOIN tickets t ON t.id=e.ticket_id ORDER BY e.created_at DESC LIMIT 200")
        denied = await db.all(
            "SELECT a.type, a.params, a.decision_reason, a.created_at, a.dry_run, t.number, t.id AS ticket_id FROM actions a "
            "JOIN tickets t ON t.id=a.ticket_id WHERE a.decision='deny' ORDER BY a.created_at DESC LIMIT 100")
        audit_rows = await db.all("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200")
    return jsonable({"events": events, "denied_actions": denied, "audit": audit_rows})


# ---------------------------------------------------------------------------
# Settings + integrations + store
# ---------------------------------------------------------------------------
@router.get("/settings")
async def get_settings(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        s = await db.one("SELECT s.*, t.name, t.slug FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
        integ = await db.all("SELECT kind, config, status FROM integrations")
    async with system_session() as sdb:
        routes = await sdb.all("SELECT channel, external_key FROM channel_routes WHERE tenant_id=:t", t=ctx.tenant_id)
    for i in integ:  # never send secrets back
        i["config"] = {k: ("••••" if any(x in k for x in ("token", "key", "secret")) and v else v) for k, v in (i["config"] or {}).items()}
    return jsonable({"settings": s, "integrations": integ, "routes": routes, "public_base_url": settings.public_base_url,
                     "inbound_email_domain": settings.inbound_email_domain, "platform_email": bool(settings.brevo_api_key and settings.email_from)})


SETTABLE = {"brand_voice", "business_description", "website_url", "default_autonomy", "owner_phone", "owner_wa_approvals",
            "debounce_ms", "sla_minutes", "brand_color", "widget_greeting", "languages", "ticket_prefix", "setup_done"}


@router.patch("/settings")
async def patch_settings(body: dict, ctx: Ctx = CtxDep):
    fields = {k: v for k, v in body.items() if k in SETTABLE}
    if not fields:
        return {"ok": True}
    async with tenant_session(ctx.tenant_id) as db:
        await db.exec("UPDATE tenant_settings SET " + ", ".join(f"{k}=:{k}" for k in fields) + ", updated_at=now()", **fields)
        await audit(db, "human", ctx.user_name, "settings.updated", "settings", ctx.tenant_id, {"fields": list(fields)})
    return {"ok": True}


@router.post("/setup/demo-store")
async def setup_demo_store(ctx: Ctx = CtxDep):
    """Setup wizard: generate a demo storefront (AI-written catalogue now, product photos in the background)."""
    from app import demo_store

    async with tenant_session(ctx.tenant_id) as db:
        try:
            products = await demo_store.generate_catalog(db, ctx.tenant_id)
        except Exception:
            log.exception("demo store generation failed")
            raise HTTPException(502, "We couldn't generate the demo store just now. Please try again.")
    demo_store.start_images(ctx.tenant_id, products)
    async with system_session() as sdb:
        key = await sdb.val("SELECT external_key FROM channel_routes WHERE channel='webchat' AND tenant_id=:t LIMIT 1", t=ctx.tenant_id)
    return {"site_key": key, "store_url": f"/store/{key}", "products": len(products)}


@router.put("/integrations/{kind}")
async def put_integration(kind: str, body: dict, ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        cur = await db.one("SELECT config FROM integrations WHERE kind=:k", k=kind)
        merged = {**((cur or {}).get("config") or {}), **{k: v for k, v in body.items() if v not in ("••••", None, "")}}
        from app.core import seal, unseal

        sealed = seal(merged)
        merged = unseal(sealed)
        await db.exec(
            "INSERT INTO integrations (tenant_id, kind, config) VALUES (:t,:k,:c) ON CONFLICT (tenant_id, kind) DO UPDATE SET config=:c, status='active'",
            t=ctx.tenant_id, k=kind, c=sealed,
        )
    if kind == "whatsapp" and merged.get("phone_number_id"):
        async with system_session() as sdb:
            await sdb.exec("INSERT INTO channel_routes (channel, external_key, tenant_id) VALUES ('whatsapp',:k,:t) "
                           "ON CONFLICT (channel, external_key) DO UPDATE SET tenant_id=:t", k=merged["phone_number_id"], t=ctx.tenant_id)
    if kind == "email" and merged.get("inbound_address"):
        async with system_session() as sdb:
            await sdb.exec("INSERT INTO channel_routes (channel, external_key, tenant_id) VALUES ('email',:k,:t) "
                           "ON CONFLICT (channel, external_key) DO UPDATE SET tenant_id=:t", k=merged["inbound_address"].lower(), t=ctx.tenant_id)
    return {"ok": True}


@router.get("/store/orders")
async def store_orders(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all("SELECT * FROM commerce_orders ORDER BY placed_at DESC LIMIT 300"))


@router.get("/store/products")
async def store_products(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all("SELECT id, sku, title, description, category, price_paise, stock, image_uri IS NOT NULL AS has_image, "
                                     "attributes ? 'damaged_image_uri' AS has_damaged FROM commerce_products ORDER BY title"))


# ---------------------------------------------------------------------------
# Simulator: be the customer (Telegram / email / chat) or the owner's phone
# ---------------------------------------------------------------------------
@router.get("/simulator/contacts")
async def sim_contacts(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        return jsonable(await db.all(
            "SELECT DISTINCT ON (customer_phone) customer_name, customer_phone, customer_email, order_ref, status, line_items "
            "FROM commerce_orders WHERE NOT ('replacement' = ANY(tags)) ORDER BY customer_phone, placed_at DESC LIMIT 100"))


@router.post("/simulator/send")
async def sim_send(
    channel: str = Form(...), text: str = Form(""), phone: str = Form(""), email: str = Form(""), name: str = Form(""),
    file: UploadFile | None = File(None), product_photo: str = Form(""), ctx: Ctx = CtxDep,
):
    """Inject a message exactly as the channel webhook would (same ingest path)."""
    atts = []
    if file:
        atts.append(InboundAttachment(await file.read(), file.content_type or "application/octet-stream", file.filename or ""))
    elif product_photo:  # "sku:damaged" or "sku:catalog" → use the store's generated images (demo helper)
        from app.core import get_media, sniff_mime

        sku, _, kind = product_photo.partition(":")
        async with tenant_session(ctx.tenant_id) as db:
            p = await db.one("SELECT image_uri, attributes FROM commerce_products WHERE sku=:s", s=sku)
        uri = (p["attributes"] or {}).get("damaged_image_uri") if kind == "damaged" else p["image_uri"]
        if uri:
            data = await get_media(uri)
            atts.append(InboundAttachment(data, sniff_mime(data, "image/jpeg"), f"{sku}.jpg"))
    m = await ingest(ctx.tenant_id, InboundMessage(
        channel=channel, text=text, from_phone=phone or None, from_email=email or None, from_name=name or None,
        visitor_id=f"sim-{phone or email}" if channel == "webchat" else None, attachments=atts,
        thread_id=None, subject=text[:60] if channel == "email" else None, verified=True,
    ))
    return jsonable({"ok": True, "message": m})


@router.get("/simulator/thread")
async def sim_thread(channel: str, phone: str = "", email: str = "", ctx: Ctx = CtxDep):
    from app.core import norm_email, norm_phone

    async with tenant_session(ctx.tenant_id) as db:
        kind, val = ("phone", norm_phone(phone)) if phone else ("email", norm_email(email))
        cust = await db.one("SELECT customer_id FROM customer_identities WHERE kind=:k AND value=:v", k=kind, v=val)
        if not cust:
            return {"messages": [], "typing": False}
        conv = await db.one("SELECT id FROM conversations WHERE customer_id=:c AND channel=:ch ORDER BY created_at DESC LIMIT 1",
                            c=cust["customer_id"], ch=channel)
        if not conv:
            return {"messages": [], "typing": False}
        msgs = await db.all(
            "SELECT m.id, m.direction, m.text, m.transcript, m.status, m.author_type, m.created_at, m.ticket_id, "
            "(SELECT json_agg(json_build_object('id', a.id, 'kind', a.kind)) FROM attachments a WHERE a.message_id=m.id) AS attachments "
            "FROM messages m WHERE m.conversation_id=:c ORDER BY m.created_at", c=conv["id"])
        pending = await db.val("SELECT count(*) FROM messages WHERE conversation_id=:c AND direction='in' AND triage IS NULL", c=conv["id"])
        running = await db.val(
            "SELECT count(*) FROM agent_runs r JOIN messages m ON m.id=r.trigger_message_id WHERE m.conversation_id=:c AND r.status='running'", c=conv["id"])
    return jsonable({"messages": msgs, "typing": bool(pending or running)})


@router.get("/simulator/owner")
async def sim_owner(ctx: Ctx = CtxDep):
    async with tenant_session(ctx.tenant_id) as db:
        rows = await db.all(
            "SELECT a.*, t.number, ac.value_paise FROM approvals a JOIN tickets t ON t.id=a.ticket_id LEFT JOIN actions ac ON ac.id=a.action_id "
            "WHERE a.kind='action' AND NOT t.is_simulation ORDER BY a.created_at DESC LIMIT 30")
        owner = await db.val("SELECT owner_phone FROM tenant_settings")
    return jsonable({"owner_phone": owner, "messages": [owner_message_preview(r) for r in rows]})


class Press(BaseModel):
    button_id: str


@router.post("/simulator/owner/press")
async def sim_owner_press(body: Press, ctx: Ctx = CtxDep):
    """Same handler path as a real WhatsApp button reply from the owner."""
    from app.approvals import parse_token

    parsed = parse_token(body.button_id)
    if not parsed:
        raise HTTPException(400, "invalid or tampered approval token")
    async with tenant_session(ctx.tenant_id) as db:
        owner = await db.val("SELECT owner_phone FROM tenant_settings")
        return jsonable(await decide(db, parsed[0], parsed[1], owner or "owner", "whatsapp"))

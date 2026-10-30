"""Approval service: one backend for dashboard approvals, owner WhatsApp approvals and handoffs."""

import hashlib
import hmac

from app.config import settings
from app.core import audit, enqueue


def approval_token(approval_id: str, decision: str) -> str:
    sig = hmac.new(settings.jwt_secret.encode(), f"{approval_id}:{decision}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"apr:{approval_id}:{decision}:{sig}"


def parse_token(token: str) -> tuple[str, str] | None:
    try:
        _, aid, decision, sig = token.split(":")
    except ValueError:
        return None
    if not hmac.compare_digest(approval_token(aid, decision).split(":")[-1], sig):
        return None
    return aid, decision


async def create_approval(db, *, kind: str, ticket_id, action_id=None, draft_id=None, summary: str = "",
                          suggestion: str = "", dry_run: bool = False) -> dict:
    row = await db.one(
        "INSERT INTO approvals (tenant_id, ticket_id, action_id, draft_id, kind, summary, suggestion, status) "
        "VALUES (:t,:tk,:a,:d,:k,:s,:g,:st) RETURNING *",
        t=db.tenant_id, tk=ticket_id, a=action_id, d=draft_id, k=kind, s=summary, g=suggestion,
        st="simulated" if dry_run else "pending",
    )
    await db.exec("UPDATE tickets SET status='pending_approval', updated_at=now() WHERE id=:t AND status NOT IN ('escalated')", t=ticket_id)
    if not dry_run and kind == "action":
        from app.channels import notify_owner_approval

        enqueue(notify_owner_approval, db.tenant_id, str(row["id"]), name="notify_owner")
    return row


async def decide(db, approval_id: str, decision: str, by: str, via: str, edited_text: str | None = None) -> dict:
    """decision: approve | reject. Runs the same executor the AI uses."""
    from app.tools import execute_action

    ap = await db.one("SELECT * FROM approvals WHERE id=:id FOR UPDATE", id=approval_id)
    if not ap:
        raise ValueError("approval not found")
    if ap["status"] != "pending":
        return {"status": ap["status"], "already_decided": True}
    status = "approved" if decision == "approve" else "rejected"
    await db.exec(
        "UPDATE approvals SET status=:s, decided_by=:b, decided_via=:v, decided_at=now() WHERE id=:id",
        s=status, b=by, v=via, id=approval_id,
    )
    await audit(db, "owner" if via == "whatsapp" else "human", by, f"approval.{status}", "approval", approval_id, {"via": via})
    result: dict = {"status": status}
    if ap["kind"] == "action" and ap["action_id"]:
        if status == "approved":
            await db.exec("UPDATE actions SET status='approved', updated_at=now() WHERE id=:id", id=ap["action_id"])
            action = await db.one("SELECT * FROM actions WHERE id=:id", id=ap["action_id"])
            try:
                result["result"] = await execute_action(db, action)
            except Exception as e:
                result["error"] = str(e)
        else:
            await db.exec("UPDATE actions SET status='rejected', updated_at=now() WHERE id=:id", id=ap["action_id"])
        from app.pipeline import followup_after_decision

        # small delay so the follow-up reads the committed decision
        enqueue(followup_after_decision, db.tenant_id, str(ap["ticket_id"]), str(ap["action_id"]), delay=1.5, name="followup")
    elif ap["kind"] == "draft" and ap["draft_id"]:
        from app.pipeline import send_draft

        if status == "approved":
            await send_draft(db, str(ap["draft_id"]), by, edited_text)
        else:
            await db.exec("UPDATE drafts SET status='discarded' WHERE id=:id", id=ap["draft_id"])
    elif ap["kind"] == "handoff":
        if status == "approved":  # "take over": the human owns the ticket now
            await db.exec("UPDATE tickets SET assignee=:b, status='escalated', updated_at=now() WHERE id=:t", b=by, t=ap["ticket_id"])
    remaining = await db.val("SELECT count(*) FROM approvals WHERE ticket_id=:t AND status='pending'", t=ap["ticket_id"])
    if not remaining:
        await db.exec("UPDATE tickets SET status=CASE WHEN status='pending_approval' THEN 'open' ELSE status END, updated_at=now() WHERE id=:t", t=ap["ticket_id"])
    return result

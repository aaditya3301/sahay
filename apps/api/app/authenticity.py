"""Authenticity screening for every image and PDF a customer sends (Telegram, email, WhatsApp, web chat).

Three layers, all recorded on the attachment (attachments.screening) and as fraud signals:
  1. File forensics (deterministic): editing software in image EXIF / PDF metadata, PDF modified after creation,
     the same file (exact or near-duplicate) sent by another customer or on another ticket.
  2. A forensic look by Gemini vision: what the document is, what it says (amount, date, UTR / transaction id,
     payee, app, status), signs of tampering, and how likely it is to be AI-generated.
  3. Cross-checks against the business's own records: payee is the business, amount and date fit the customer's
     orders, the transaction id hasn't been used in another claim, UPI references are well-formed, and the
     payment gateway agrees.

The verdict (genuine / suspicious / likely_fake) is shown to the resolver, raises a policy fact so money actions
need a person, and appears on the ticket. The AI never accuses the customer: it says a teammate will check.
"""

import difflib
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app import llm
from app.config import settings
from app.core import get_media, phash_distance, security_event

log = logging.getLogger("sahay.authenticity")
EDIT_TOOLS = re.compile(r"photoshop|gimp|picsart|snapseed|canva|lightroom|pixlr|meitu|fotor|facetune|polarr|illustrator|affinity|"
                        r"ilovepdf|smallpdf|sejda|pdfescape|pdf-xchange|foxit phantom|pdfelement|pdf candy|docfly|soda pdf|"
                        r"acrobat.*(edit|pro)|nitro", re.I)
SCREENABLE = ("image/", "application/pdf")


class Extracted(BaseModel):
    amount_inr: float | None = None
    paid_at: str | None = Field(None, description="date/time shown, ISO 8601 if you can, else as written")
    transaction_id: str | None = Field(None, description="UTR / UPI ref / transaction / invoice number exactly as shown")
    payer_name: str | None = None
    payee_name: str | None = Field(None, description="who received the money / who issued the bill")
    payee_upi: str | None = None
    app_or_bank: str | None = None
    status: Literal["success", "failed", "pending", "unknown"] = "unknown"
    order_ref: str | None = Field(None, description="an order number printed on it, if any")


class DocCheck(BaseModel):
    kind: Literal["payment_receipt", "upi_or_bank_screenshot", "invoice_or_bill", "delivery_proof", "product_photo",
                  "chat_screenshot", "id_document", "other"]
    summary: str = Field(description="one line: what this is")
    extracted: Extracted = Extracted()
    tampering_signs: list[str] = Field([], description="concrete visual signs of editing; empty if none")
    ai_generated_likelihood: float = Field(ge=0, le=1)
    authenticity: Literal["likely_genuine", "uncertain", "likely_forged"]
    confidence: float = Field(ge=0, le=1)


SYSTEM = """You are a document-forensics examiner for an Indian online store's support team. Customers sometimes send
forged payment screenshots, edited bills, reused or AI-generated photos to get refunds. Examine the file carefully:
- Identify what it is and read every relevant field exactly as printed (do not guess missing values).
- Look for concrete tampering signs: mismatched fonts / weights / kerning in amounts or names, digits misaligned with
  the baseline, inconsistent background or JPEG noise around numbers, cloned or blurred patches, wrong app layout for
  the named app (GPay, PhonePe, Paytm, bank apps), status-bar time or date that contradicts the receipt, impossible
  dates, a UPI reference that isn't 12 digits, totals that don't add up.
- Rate how likely the image is AI-generated (warped text, garbled small print, inconsistent UI elements, too-perfect lighting).
Be fair: a genuine, plain screenshot should be "likely_genuine". Only say "likely_forged" with specific evidence."""


def _close(a: str, b: str) -> bool:
    a, b = re.sub(r"[^a-z0-9]", "", a.lower()), re.sub(r"[^a-z0-9]", "", b.lower())
    return bool(a and b) and (a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


def file_forensics(data: bytes, mime: str) -> list[dict]:
    signals: list[dict] = []
    if mime.startswith("image/"):
        try:
            from PIL import Image

            exif = Image.open(io.BytesIO(data)).getexif()
            software = str(exif.get(0x0131) or "")  # Software
            if software and EDIT_TOOLS.search(software):
                signals.append({"kind": "edited_with_software", "score": 0.35, "evidence": {"software": software[:80]}})
            original, modified = str(exif.get_ifd(0x8769).get(0x9003) or ""), str(exif.get(0x0132) or "")  # DateTimeOriginal, DateTime
            if original and modified and original[:16] != modified[:16]:
                signals.append({"kind": "metadata_modified_after_capture", "score": 0.2, "evidence": {"taken": original, "modified": modified}})
        except Exception:
            pass
    elif mime == "application/pdf":
        head = data.decode("latin-1", errors="ignore") if len(data) <= 260_000 else             data[:200_000].decode("latin-1", errors="ignore") + data[-60_000:].decode("latin-1", errors="ignore")
        producer = " ".join(re.findall(r"/(?:Producer|Creator)\s*\(([^)]{1,120})\)", head))
        if producer and EDIT_TOOLS.search(producer):
            signals.append({"kind": "pdf_edited_with_tool", "score": 0.35, "evidence": {"producer": producer[:120]}})
        cd = re.search(r"/CreationDate\s*\(D:(\d{12})", head)
        md = re.search(r"/ModDate\s*\(D:(\d{12})", head)
        if cd and md and md.group(1) > cd.group(1):
            signals.append({"kind": "pdf_modified_after_creation", "score": 0.25, "evidence": {"created": cd.group(1), "modified": md.group(1)}})
        if head.count("%%EOF") > 1:
            signals.append({"kind": "pdf_incrementally_edited", "score": 0.15, "evidence": {"revisions": head.count("%%EOF")}})
    return signals


async def _reuse(db, att: dict, ticket_id) -> list[dict]:
    signals = []
    dup = await db.one("SELECT a.id, m.customer_id, m.ticket_id FROM attachments a JOIN messages m ON m.id=a.message_id "
                       "WHERE a.sha256=:s AND a.id<>:id AND m.ticket_id IS DISTINCT FROM CAST(:t AS uuid) LIMIT 1",
                       s=att["sha256"], id=att["id"], t=ticket_id)
    if dup:
        signals.append({"kind": "duplicate_file", "score": 0.5, "evidence": {"other_attachment": str(dup["id"])}})
    elif att.get("phash"):
        for p in await db.all("SELECT a.id, a.phash FROM attachments a JOIN messages m ON m.id=a.message_id WHERE a.phash IS NOT NULL "
                              "AND a.id<>:id AND m.ticket_id IS DISTINCT FROM CAST(:t AS uuid) ORDER BY a.created_at DESC LIMIT 500",
                              id=att["id"], t=ticket_id):
            if phash_distance(att["phash"], p["phash"]) <= 6:
                signals.append({"kind": "near_duplicate_image", "score": 0.4, "evidence": {"other_attachment": str(p["id"])}})
                break
    return signals


async def _gateway_paid(db, order_ref: str) -> bool | None:
    """True/False when the payment gateway (or our paid tag) knows the order; None when we can't tell."""
    o = await db.one("SELECT status, tags FROM commerce_orders WHERE upper(order_ref)=upper(:r)", r=order_ref)
    if not o:
        return None
    if "cashfree" in (o["tags"] or []):
        if "paid" in (o["tags"] or []):
            return True
        try:
            from app import payments

            return (await payments.cashfree_order(order_ref)).get("order_status") == "PAID"
        except Exception:
            return None
    return None


async def screen(ctx, att: dict, orders: list[dict]) -> dict:
    """Screen one attachment for authenticity. ctx is the pipeline's ToolContext."""
    if att.get("screening"):
        return att["screening"]
    data = await get_media(att["uri"])
    signals = file_forensics(data, att["mime"]) + await _reuse(ctx.db, att, ctx.ticket["id"])
    prof = ctx.profile or {}
    pay = prof.get("sales") or {}  # payee_names / upi_ids the business receives money under
    business = await ctx.db.val("SELECT name FROM tenants WHERE id=:t", t=ctx.tenant_id)
    payees = [business] + list(pay.get("payee_names") or [])
    upis = [u.lower() for u in pay.get("upi_ids") or []]
    order_lines = "; ".join(f"#{o['order_ref']} ₹{o['total_inr']:.0f} {o['payment_method']} {o['status']} placed {str(o.get('placed_at') or '')[:10]}"
                            for o in orders[:5]) or "(no orders on file)"
    check = await llm.structured(
        DocCheck, model=settings.model_vision, temperature=0, purpose="doc_forensics", tenant_id=ctx.tenant_id,
        thinking=settings.thinking_vision or None, system=SYSTEM,
        contents=[f"Business: {business}. Money paid to this business should name: {', '.join(payees)}"
                  + (f" or UPI {', '.join(upis)}" if upis else "") + f". Today is {datetime.now(timezone.utc).date()}.\n"
                  f"This customer's orders: {order_lines}.\nThe file the customer sent:",
                  llm.part_bytes(data, att["mime"])])
    x = check.extracted
    money_doc = check.kind in ("payment_receipt", "upi_or_bank_screenshot", "invoice_or_bill")
    if money_doc:
        if x.payee_name and not any(_close(x.payee_name, p) for p in payees) and not (x.payee_upi and x.payee_upi.lower() in upis):
            signals.append({"kind": "payee_not_this_business", "score": 0.4, "evidence": {"payee": x.payee_name, "upi": x.payee_upi}})
        if x.amount_inr and orders and not any(abs(x.amount_inr - o["total_inr"]) <= 1 or abs(x.amount_inr - o.get("refunded_inr", 0)) <= 1 for o in orders):
            signals.append({"kind": "amount_matches_no_order", "score": 0.3, "evidence": {"amount": x.amount_inr, "orders": [o["total_inr"] for o in orders[:5]]}})
        if x.transaction_id and len(re.sub(r"\W", "", x.transaction_id)) >= 6:
            tid = re.sub(r"\W", "", x.transaction_id).upper()
            reused = await ctx.db.one(
                "SELECT a.id FROM attachments a JOIN messages m ON m.id=a.message_id WHERE a.id<>:id "
                "AND upper(regexp_replace(a.screening->'extracted'->>'transaction_id', '\\W', '', 'g'))=:tid "
                "AND m.ticket_id IS DISTINCT FROM CAST(:t AS uuid) LIMIT 1", id=att["id"], tid=tid, t=ctx.ticket["id"])
            if reused:
                signals.append({"kind": "transaction_id_used_before", "score": 0.7, "evidence": {"transaction_id": x.transaction_id, "other_attachment": str(reused["id"])}})
            upi_app = re.search(r"upi|gpay|google pay|phonepe|paytm|bhim", (x.app_or_bank or "") + " " + check.summary, re.I)
            if upi_app and not re.fullmatch(r"\d{12}", tid) and not re.fullmatch(r"T\d{20,24}", tid):
                signals.append({"kind": "upi_reference_malformed", "score": 0.2, "evidence": {"transaction_id": x.transaction_id}})
        if x.paid_at:
            try:
                when = datetime.fromisoformat(x.paid_at.replace("Z", "+00:00"))
                when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
                if when > datetime.now(timezone.utc) + timedelta(days=1):
                    signals.append({"kind": "date_in_future", "score": 0.4, "evidence": {"paid_at": x.paid_at}})
            except ValueError:
                pass
        ref = (x.order_ref or "").lstrip("#")
        if ref and x.status == "success":
            paid = await _gateway_paid(ctx.db, ref)
            if paid is False:
                signals.append({"kind": "gateway_shows_unpaid", "score": 0.6, "evidence": {"order": ref}})
            elif paid is True:
                signals.append({"kind": "gateway_confirms_payment", "score": -0.3, "evidence": {"order": ref}})
    for t in check.tampering_signs[:4]:
        signals.append({"kind": "visual_tampering", "score": 0.15, "evidence": {"sign": t[:160]}})

    score = sum(s["score"] for s in signals) + {"likely_forged": 0.45, "uncertain": 0.1, "likely_genuine": 0.0}[check.authenticity] * check.confidence \
        + (0.45 * check.ai_generated_likelihood if check.ai_generated_likelihood >= 0.5 else 0)
    score = round(max(0.0, min(1.0, score)), 2)
    verdict = "likely_fake" if score >= 0.6 else ("suspicious" if score >= 0.3 else "genuine")
    result = {"attachment_id": str(att["id"]), "kind": check.kind, "summary": check.summary, "verdict": verdict, "score": score,
              "ai_generated_likelihood": round(check.ai_generated_likelihood, 2), "model_view": check.authenticity,
              "extracted": x.model_dump(), "tampering_signs": check.tampering_signs, "signals": signals,
              "filename": att.get("filename"), "checked_at": datetime.now(timezone.utc).isoformat()}
    await ctx.db.exec("UPDATE attachments SET screening=:s WHERE id=:id", s=json.loads(json.dumps(result, default=str)), id=att["id"])
    for s in signals:
        if s["score"] > 0:
            await ctx.db.exec("INSERT INTO fraud_signals (tenant_id, customer_id, ticket_id, attachment_id, kind, score, evidence) VALUES (:t,:c,:tk,:a,:k,:s,:e)",
                              t=ctx.tenant_id, c=ctx.customer["id"], tk=ctx.ticket["id"], a=att["id"], k=s["kind"], s=s["score"], e=s["evidence"])
    if verdict != "genuine":
        await security_event(ctx.db, "suspicious_document", f"{check.kind.replace('_', ' ').title()} looks {verdict.replace('_', ' ')} "
                             f"(score {score}): " + ", ".join(s["kind"].replace("_", " ") for s in signals if s["score"] > 0)[:300],
                             ctx.ticket["id"], "high" if verdict == "likely_fake" else "medium", {"attachment_id": str(att["id"])})
        if score > float(ctx.customer.get("fraud_score") or 0):
            await ctx.db.exec("UPDATE customers SET fraud_score=:f WHERE id=:c", f=score, c=ctx.customer["id"])
            ctx.customer["fraud_score"] = score
    return result


async def screen_new(ctx, attachments: list[dict], orders: list[dict]) -> list[dict]:
    out = []
    for att in attachments:
        if not att["mime"].startswith(SCREENABLE):
            continue
        try:
            out.append(await screen(ctx, att, orders))
        except Exception:
            log.exception("screening failed for %s", att["id"])
    return out


def for_resolver(results: list[dict]) -> str:
    if not results:
        return "(no images or documents)"
    lines = []
    for r in results:
        why = ", ".join(s["kind"].replace("_", " ") for s in r["signals"] if s["score"] > 0) or "no problems found"
        x = {k: v for k, v in (r.get("extracted") or {}).items() if v not in (None, "", "unknown")}
        lines.append(f"- attachment {r['attachment_id']}: {r['kind'].replace('_', ' ')} — {r['verdict'].upper()} (score {r['score']}). "
                     f"{r['summary']} Read: {json.dumps(x, ensure_ascii=False)}. Signals: {why}.")
    return "\n".join(lines)

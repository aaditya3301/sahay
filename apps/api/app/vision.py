"""Photo claims + fraud guard: vision verdict (Gemini) + perceptual hashing (reuse / catalog images)."""

from typing import Literal

from pydantic import BaseModel, Field

from app import llm
from app.config import settings
from app.core import get_media, phash_distance, sniff_mime


class VisionVerdict(BaseModel):
    matches_ordered_product: bool
    product_match_confidence: float = Field(ge=0, le=1)
    damage_visible: bool
    damage_type: Literal["broken", "scratched", "stained", "missing_part", "wrong_item", "spoiled", "leaked", "none", "other"]
    severity: Literal["none", "minor", "major", "total"]
    image_quality_ok: bool
    looks_edited_or_screenshot: bool
    looks_like_studio_catalog_photo: bool
    notes: str


REUSE_DISTANCE = 8


async def analyze_attachment(ctx, attachment_id: str, order_ref: str) -> dict:
    from app.tools import owned_order

    att = await ctx.db.one("SELECT * FROM attachments WHERE id=:id", id=attachment_id)
    if not att or att["kind"] != "image":
        return {"error": "image attachment not found"}
    if att["analysis"]:
        return att["analysis"]
    order, belongs = await owned_order(ctx, order_ref)
    if not order or not belongs:
        return {"error": "order not found for this customer; ask for the order number first"}
    shop = await ctx.shop()
    products = [await shop.get_product(li["sku"]) for li in order["line_items"]]
    products = [p for p in products if p]

    # --- perceptual-hash checks (deterministic) -------------------------
    signals: list[dict] = []
    if att["phash"]:
        prior = await ctx.db.all(
            "SELECT a.id, a.phash, m.customer_id, m.ticket_id FROM attachments a JOIN messages m ON m.id=a.message_id "
            "WHERE a.kind='image' AND a.phash IS NOT NULL AND a.id<>:id", id=attachment_id,
        )
        for p in prior:
            d = phash_distance(att["phash"], p["phash"])
            if d <= REUSE_DISTANCE and str(p["ticket_id"]) != str(ctx.ticket["id"]):
                signals.append({"kind": "reused_image", "score": 0.5, "evidence": {"distance": d, "other_attachment": str(p["id"]), "same_customer": str(p["customer_id"]) == str(ctx.customer["id"])}})
                break
        catalog = await ctx.db.all("SELECT sku, image_phash FROM commerce_products WHERE image_phash IS NOT NULL")
        for c in catalog:
            d = phash_distance(att["phash"], c["image_phash"])
            if d <= REUSE_DISTANCE + 4:
                signals.append({"kind": "catalog_image", "score": 0.6, "evidence": {"distance": d, "sku": c["sku"]}})
                break

    # --- vision verdict --------------------------------------------------
    parts = [llm.part_bytes(await get_media(att["uri"]), att["mime"])]
    for p in products[:3]:
        if p.get("image_uri"):
            try:
                parts.append(f"Catalog image of ordered product {p['sku']} — {p['title']}:")
                img = await get_media(p["image_uri"])
                parts.append(llm.part_bytes(img, sniff_mime(img, "image/jpeg")))
            except Exception:
                pass
    desc = "; ".join(f"{p['sku']}: {p['title']} ({p['category']}) — {p['description'][:160]}" for p in products)
    parts.insert(0, f"The FIRST image is the customer's photo for a complaint. Ordered products: {desc}. "
                    "Compare it with the catalog images that follow. Is this the ordered product, and is damage visible?")
    verdict = await llm.structured(
        VisionVerdict, model=settings.model_vision, contents=parts, temperature=0,
        purpose="vision_claim", tenant_id=ctx.tenant_id, thinking=settings.thinking_vision or None,
        system="You are a careful claims inspector for an online store. Be strict: only mark damage_visible when it is clearly visible.",
    )
    if not verdict.matches_ordered_product:
        signals.append({"kind": "product_mismatch", "score": 0.4, "evidence": {"confidence": verdict.product_match_confidence}})
    if verdict.looks_edited_or_screenshot:
        signals.append({"kind": "edited_or_screenshot", "score": 0.2, "evidence": {}})
    if verdict.looks_like_studio_catalog_photo and not any(s["kind"] == "catalog_image" for s in signals):
        signals.append({"kind": "catalog_like_photo", "score": 0.4, "evidence": {}})
    claims = await ctx.db.val(
        "SELECT count(*) FROM actions WHERE customer_id=:c AND type IN ('create_replacement','issue_refund','create_return') "
        "AND created_at > now()-interval '90 days' AND NOT dry_run", c=ctx.customer["id"],
    )
    if (claims or 0) >= 3:
        signals.append({"kind": "serial_claimer", "score": 0.3, "evidence": {"claims_90d": claims}})
    if order.get("delivered_at") is None and order["status"] != "delivered":
        signals.append({"kind": "claim_before_delivery", "score": 0.5, "evidence": {"status": order["status"]}})

    fraud_score = min(1.0, sum(s["score"] for s in signals))
    reused = any(s["kind"] in ("reused_image", "catalog_image") for s in signals)
    photo_verified = verdict.damage_visible and verdict.matches_ordered_product and verdict.product_match_confidence >= 0.6 and not reused
    analysis = {
        "attachment_id": attachment_id, **verdict.model_dump(), "photo_verified": photo_verified,
        "reused_image": reused, "fraud_score": round(fraud_score, 2), "fraud_signals": signals,
    }
    await ctx.db.exec("UPDATE attachments SET analysis=:a WHERE id=:id", a=analysis, id=attachment_id)
    for s in signals:
        await ctx.db.exec(
            "INSERT INTO fraud_signals (tenant_id, customer_id, ticket_id, attachment_id, kind, score, evidence) VALUES (:t,:c,:tk,:a,:k,:s,:e)",
            t=ctx.tenant_id, c=ctx.customer["id"], tk=ctx.ticket["id"], a=attachment_id, k=s["kind"], s=s["score"], e=s["evidence"],
        )
    if fraud_score > float(ctx.customer.get("fraud_score") or 0):
        await ctx.db.exec("UPDATE customers SET fraud_score=:f WHERE id=:c", f=fraud_score, c=ctx.customer["id"])
        ctx.customer["fraud_score"] = fraud_score
    return {k: analysis[k] for k in ("photo_verified", "matches_ordered_product", "damage_visible", "damage_type", "severity", "reused_image", "fraud_score", "notes")}

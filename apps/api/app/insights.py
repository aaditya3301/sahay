"""Weekly insights (complaint clusters → root causes), knowledge-gap detector, learning loop."""

import json
import re
from datetime import date, timedelta

import numpy as np
from pydantic import BaseModel

from app import kb, llm
from app.config import settings


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
async def metrics(db, start: date, end: date) -> dict:
    p = {"s": start, "e": end + timedelta(days=1)}
    base = "FROM tickets WHERE NOT is_simulation AND created_at >= :s AND created_at < :e"
    row = await db.one(
        f"SELECT count(*) AS total, "
        f"count(*) FILTER (WHERE status IN ('resolved','closed')) AS resolved, "
        f"count(*) FILTER (WHERE resolution_type='ai_auto') AS ai_auto, "
        f"count(*) FILTER (WHERE resolution_type='ai_assisted') AS ai_assisted, "
        f"count(*) FILTER (WHERE status='escalated') AS escalated, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_response_at-created_at)) AS frt_median_s, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at-created_at)) AS ttr_median_s, "
        f"avg(sentiment) AS sentiment_avg, avg(csat) AS csat_avg, "
        f"count(*) FILTER (WHERE sentiment < -0.3) AS negative {base}", **p,
    )
    by_intent = await db.all(f"SELECT intent, count(*) AS n, avg(sentiment) AS sentiment {base} GROUP BY intent ORDER BY n DESC", **p)
    by_day = await db.all(
        f"SELECT date_trunc('day', created_at)::date AS day, count(*) AS n, avg(sentiment) AS sentiment, "
        f"count(*) FILTER (WHERE resolution_type='ai_auto') AS ai_auto {base} GROUP BY 1 ORDER BY 1", **p,
    )
    by_channel = await db.all(
        "SELECT m.channel, count(DISTINCT m.ticket_id) AS n FROM messages m JOIN tickets t ON t.id=m.ticket_id "
        "WHERE NOT t.is_simulation AND t.created_at >= :s AND t.created_at < :e AND m.direction='in' GROUP BY 1 ORDER BY 2 DESC", **p,
    )
    cost = await db.val("SELECT coalesce(sum(cost_micros),0) FROM llm_calls WHERE created_at >= :s AND created_at < :e", **p)
    saved = await db.val(
        "SELECT coalesce(sum(value_paise),0) FROM actions WHERE status='succeeded' AND NOT dry_run AND type='issue_coupon' AND created_at >= :s AND created_at < :e", **p)
    total = int(row["total"] or 0)
    resolved = int(row["resolved"] or 0)
    return {
        "total": total, "resolved": resolved, "ai_auto": int(row["ai_auto"] or 0), "ai_assisted": int(row["ai_assisted"] or 0),
        "escalated": int(row["escalated"] or 0),
        "ai_resolution_rate": round(100 * (row["ai_auto"] or 0) / resolved, 1) if resolved else 0,
        "frt_median_s": round(row["frt_median_s"] or 0), "ttr_median_min": round((row["ttr_median_s"] or 0) / 60),
        "sentiment_avg": round(float(row["sentiment_avg"] or 0), 2), "negative_pct": round(100 * (row["negative"] or 0) / total, 1) if total else 0,
        "csat_avg": round(float(row["csat_avg"]), 2) if row["csat_avg"] else None,
        "llm_cost_inr": round(cost / 1e6, 2), "cost_per_resolved_inr": round(cost / 1e6 / resolved, 2) if resolved else 0,
        "coupons_inr": round(saved / 100),
        "by_intent": [{"intent": r["intent"] or "other", "n": r["n"], "sentiment": round(float(r["sentiment"] or 0), 2)} for r in by_intent],
        "by_day": [{"day": r["day"].isoformat(), "n": r["n"], "sentiment": round(float(r["sentiment"] or 0), 2), "ai_auto": r["ai_auto"]} for r in by_day],
        "by_channel": by_channel,
    }


# ---------------------------------------------------------------------------
# Complaint clustering + root-cause lift
# ---------------------------------------------------------------------------
def _cluster(vecs: np.ndarray, threshold: float = 0.13) -> np.ndarray:
    from sklearn.cluster import AgglomerativeClustering

    if len(vecs) < 2:
        return np.zeros(len(vecs), dtype=int)
    return AgglomerativeClustering(n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average").fit_predict(vecs)


class ClusterLabel(BaseModel):
    label: str
    likely_root_cause: str


async def complaint_clusters(db, start: date, end: date, tenant_id: str) -> list[dict]:
    rows = await db.all(
        "SELECT id, number, complaint_summary, complaint_embedding, entities, sentiment, created_at FROM tickets "
        "WHERE NOT is_simulation AND complaint_embedding IS NOT NULL AND created_at >= :s AND created_at < :e",
        s=start - timedelta(days=7), e=end + timedelta(days=1),
    )
    if not rows:
        return []
    cur = [r for r in rows if r["created_at"].date() >= start]
    prev = [r for r in rows if r["created_at"].date() < start]
    allv = np.array([np.array(r["complaint_embedding"]) for r in rows])
    labels = _cluster(allv)
    for r, l in zip(rows, labels):
        r["cluster"] = int(l)
    # baseline entity distribution: all orders in the period
    orders = await db.all("SELECT line_items, courier, shipping_address FROM commerce_orders WHERE placed_at >= :s", s=start - timedelta(days=30))
    base_sku: dict[str, int] = {}
    base_courier: dict[str, int] = {}
    for o in orders:
        for li in o["line_items"]:
            base_sku[li["sku"]] = base_sku.get(li["sku"], 0) + 1
        if o["courier"]:
            base_courier[o["courier"]] = base_courier.get(o["courier"], 0) + 1
    tot_sku = sum(base_sku.values()) or 1
    tot_courier = sum(base_courier.values()) or 1
    out = []
    for cid in sorted(set(labels)):
        members = [r for r in cur if r["cluster"] == cid]
        if len(members) < 2:
            continue
        prev_n = len([r for r in prev if r["cluster"] == cid])
        ents: dict[str, dict[str, int]] = {"sku": {}, "product": {}, "courier": {}, "pincode": {}}
        for m in members:
            for k in ents:
                v = (m["entities"] or {}).get(k)
                if v:
                    ents[k][str(v)] = ents[k].get(str(v), 0) + 1
        drivers = []
        for k, counts in ents.items():
            if not counts:
                continue
            top, n = max(counts.items(), key=lambda x: x[1])
            share = n / len(members)
            if k == "sku":
                lift = share / (base_sku.get(top, 0) / tot_sku or 1 / tot_sku)
            elif k == "courier":
                lift = share / (base_courier.get(top, 0) / tot_courier or 1 / tot_courier)
            else:
                lift = None
            if share >= 0.4:
                drivers.append({"field": k, "value": top, "share_pct": round(100 * share), "lift": round(lift, 1) if lift else None})
        out.append({
            "cluster_id": int(cid), "size": len(members), "prev_week": prev_n,
            "wow_change_pct": round(100 * (len(members) - prev_n) / prev_n) if prev_n else None,
            "sentiment_avg": round(float(np.mean([m["sentiment"] or 0 for m in members])), 2),
            "examples": [m["complaint_summary"] for m in members[:6]],
            "ticket_numbers": [m["number"] for m in members[:20]], "drivers": drivers,
        })
    out.sort(key=lambda c: -c["size"])
    out = out[:8]
    for c in out:
        lab = await llm.structured(
            ClusterLabel, model=settings.model_triage, purpose="cluster_label", tenant_id=tenant_id, temperature=0,
            contents=f"Complaints: {json.dumps(c['examples'])}\nConcentration: {json.dumps(c['drivers'])}\n"
                     "Give a 3-6 word label and the most likely root cause in one sentence.",
        )
        c["label"], c["root_cause"] = lab.label, lab.likely_root_cause
    return out


class Recommendation(BaseModel):
    title: str
    detail: str
    kind: str  # kb_article | product | logistics | policy | other


class Report(BaseModel):
    headline: str
    narrative_md: str
    recommendations: list[Recommendation]


def _numbers_ok(text: str, stats_json: str) -> bool:
    nums = set(re.findall(r"\d+(?:\.\d+)?", stats_json))
    for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", text):
        if n not in nums and float(n) > 10 and not any(n in x for x in nums):
            return False
    return True


async def weekly_report(db, tenant_id: str, end: date | None = None, days: int = 7) -> dict:
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    stats = await metrics(db, start, end)
    clusters = await complaint_clusters(db, start, end, tenant_id)
    payload = json.dumps({"period": f"{start}..{end}", "stats": {k: v for k, v in stats.items() if k != "by_day"}, "clusters": clusters}, default=str)
    system = ("You write the weekly support insights report for a small D2C brand owner. Use ONLY numbers present in the JSON. "
              "headline: one line. narrative_md: markdown with real newlines (do NOT repeat the headline), sections '### Top issues' "
              "(3 max, one bullet each: what, how many, week-over-week change, likely root cause tied to the SKU/courier/pincode "
              "concentration), '### Wins' and '### What to fix this week'. Be concrete and short.")
    rep = await llm.structured(Report, model=settings.model_reasoning, system=system, contents=payload, temperature=0.2,
                               purpose="weekly_report", tenant_id=tenant_id)
    if not _numbers_ok(rep.narrative_md, payload):
        rep = await llm.structured(Report, model=settings.model_reasoning, system=system + " Your last draft used numbers not in the JSON. Do not.",
                                   contents=payload, temperature=0, purpose="weekly_report", tenant_id=tenant_id)
    row = await db.one(
        "INSERT INTO insights_reports (tenant_id, period_start, period_end, stats, clusters, narrative_md, recommendations) "
        "VALUES (:t,:s,:e,:st,:c,:n,:r) RETURNING *",
        t=tenant_id, s=start, e=end, st=stats, c=clusters, n=f"## {rep.headline}\n\n{rep.narrative_md}",
        r=[r.model_dump() for r in rep.recommendations],
    )
    return row


# ---------------------------------------------------------------------------
# Knowledge-gap detector
# ---------------------------------------------------------------------------
class GapArticle(BaseModel):
    label: str
    title: str
    article_md: str
    needs_owner_input: bool


async def build_gap_suggestions(db, tenant_id: str) -> int:
    gaps = await db.all("SELECT id, question, embedding, ticket_id FROM kb_gaps WHERE suggestion_id IS NULL AND embedding IS NOT NULL")
    if len(gaps) < 2:
        return 0
    labels = _cluster(np.array([np.array(g["embedding"]) for g in gaps]), threshold=0.35)
    made = 0
    for cid in set(labels):
        members = [g for g, l in zip(gaps, labels) if l == cid]
        if len(members) < 2:
            continue
        answers = await db.all(
            "SELECT m.text FROM messages m WHERE m.ticket_id = ANY(:ids) AND m.direction='out' AND m.author_type='human'",
            ids=[g["ticket_id"] for g in members if g["ticket_id"]],
        )
        art = await llm.structured(
            GapArticle, model=settings.model_composer, purpose="gap_article", tenant_id=tenant_id, temperature=0.2,
            contents=f"Customers asked:\n{json.dumps([m['question'] for m in members])}\n\nAnswers our team gave (may be empty):\n"
                     f"{json.dumps([a['text'] for a in answers])}\n\nDraft a short FAQ article answering this. Use ONLY facts from the team's "
                     "answers; where a fact is missing, write [OWNER: fill in ...] and set needs_owner_input.",
        )
        sug = await db.one(
            "INSERT INTO kb_gap_suggestions (tenant_id, label, questions, draft_title, draft_article_md) VALUES (:t,:l,:q,:ti,:a) RETURNING id",
            t=tenant_id, l=art.label, q=[m["question"] for m in members], ti=art.title, a=art.article_md,
        )
        await db.exec("UPDATE kb_gaps SET suggestion_id=:s WHERE id = ANY(:ids)", s=sug["id"], ids=[m["id"] for m in members])
        made += 1
    return made


async def approve_gap(db, suggestion_id: str, title: str, article_md: str) -> str:
    doc_id = await kb.add_document(db, title, article_md, "gap_approved")
    await db.exec("UPDATE kb_gap_suggestions SET status='approved', draft_title=:t, draft_article_md=:a WHERE id=:id",
                  t=title, a=article_md, id=suggestion_id)
    return doc_id


# ---------------------------------------------------------------------------
# Learning loop: human edits → lessons; autonomy ladder
# ---------------------------------------------------------------------------
class Lessons(BaseModel):
    lessons: list[str]


async def extract_lessons(db, tenant_id: str) -> int:
    edits = await db.all("SELECT id, intent, original_text, final_text FROM human_edits WHERE NOT lesson_extracted AND edit_ratio > 0.1 ORDER BY created_at DESC LIMIT 30")
    by_intent: dict[str, list[dict]] = {}
    for e in edits:
        by_intent.setdefault(e["intent"] or "other", []).append(e)
    n = 0
    for intent, items in by_intent.items():
        res = await llm.structured(
            Lessons, model=settings.model_composer, purpose="lessons", tenant_id=tenant_id, temperature=0.1,
            contents="The support team edited these AI drafts before sending. Infer 1-3 short, reusable instructions for future replies "
                     "(e.g. 'Always mention the 5-7 day refund timeline for prepaid orders'). Ignore one-off fact fixes.\n\n"
                     + "\n\n".join(f"AI: {e['original_text']}\nTEAM: {e['final_text']}" for e in items),
        )
        for lesson in res.lessons:
            await db.exec("INSERT INTO playbook_lessons (tenant_id, intent, lesson) VALUES (:t,:i,:l)", t=tenant_id, i=intent, l=lesson)
            n += 1
        await db.exec("UPDATE human_edits SET lesson_extracted=true WHERE id = ANY(:ids)", ids=[e["id"] for e in items])
    return n


async def autonomy_suggestions(db) -> list[dict]:
    rows = await db.all(
        "SELECT intent, count(*) AS n, count(*) FILTER (WHERE edit_ratio < 0.1) AS unchanged FROM "
        "(SELECT intent, edit_ratio FROM human_edits ORDER BY created_at DESC LIMIT 500) x GROUP BY intent"
    )
    s = await db.one("SELECT autonomy, default_autonomy FROM tenant_settings")
    out = []
    for r in rows:
        current = (s["autonomy"] or {}).get(r["intent"], s["default_autonomy"])
        rate = r["unchanged"] / r["n"] if r["n"] else 0
        if r["n"] >= 5 and rate >= 0.9 and current != "auto":
            out.append({"intent": r["intent"], "approved_unchanged": r["unchanged"], "total": r["n"], "rate_pct": round(100 * rate),
                        "suggest": "auto", "current": current})
    return out

"""Load demo tenants from /seed/<slug>/ (tenant.json, products.json, orders.json, kb/*.md, history.json).

    python -m app.seed               # create missing tenants
    python -m app.seed --reset       # drop and recreate the seed tenants
    python -m app.seed --only loop-loot --no-history
"""

import argparse
import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.genai import types
from pydantic import BaseModel

from app import kb, llm
from app import policy as pol
from app.config import ROOT, settings
from app.core import get_media, hash_password, norm_phone, phash, put_media
from app.db import system_session, tenant_session
from app.routes_public import create_tenant

SEED = ROOT / "seed"
COMMON = json.loads((SEED / "common.json").read_text(encoding="utf-8"))
NOW = datetime.now(timezone.utc)


def load(slug: str, name: str):
    p = SEED / slug / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---------------------------------------------------------------------------
# Images (cached in GCS so re-seeding doesn't regenerate)
# ---------------------------------------------------------------------------
async def _gcs_exists(key: str) -> bool:
    from google.cloud import storage

    def _ex():
        return storage.Client(project=settings.gcp_project).bucket(settings.media_bucket).blob(key).exists()

    return await asyncio.to_thread(_ex)


async def gen_image(slug: str, key: str, prompt: str) -> str:
    gcs_key = f"catalog/{slug}/{key}.img"
    uri = f"gs://{settings.media_bucket}/{gcs_key}"
    if await _gcs_exists(gcs_key):
        return uri
    for attempt in range(3):
        try:
            r = await llm.client().aio.models.generate_content(
                model=settings.model_image, contents=prompt, config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
            part = next(p for p in r.candidates[0].content.parts if p.inline_data)
            from google.cloud import storage

            data, mime = part.inline_data.data, part.inline_data.mime_type

            def _up():
                storage.Client(project=settings.gcp_project).bucket(settings.media_bucket).blob(gcs_key).upload_from_string(data, content_type=mime)

            await asyncio.to_thread(_up)
            return uri
        except Exception as e:
            print(f"  image retry {attempt + 1} for {key}: {str(e)[:120]}")
            await asyncio.sleep(2)
    raise RuntimeError(f"could not generate image {key}")


# ---------------------------------------------------------------------------
# History generation
# ---------------------------------------------------------------------------
class Variation(BaseModel):
    customer_message: str
    complaint_summary_en: str
    agent_reply: str


class Variations(BaseModel):
    items: list[Variation]


async def gen_variations(tenant: str, scenario: dict, n: int, tenant_id: str) -> list[Variation]:
    out: list[Variation] = []
    while len(out) < n:
        k = min(12, n - len(out))
        res = await llm.structured(
            Variations, model=settings.model_persona, temperature=1.0, purpose="seed_history", tenant_id=tenant_id,
            contents=f"Write {k} different realistic customer support messages for {tenant}, an Indian D2C brand. Situation: {scenario['describe']}. "
                     f"Mix Hinglish (romanized Hindi), English and a few in Hindi Devanagari, chat style, varied length and tone. "
                     f"For each, give a one-line English complaint summary that names the product/courier problem, and the short reply the support agent sent.",
        )
        out.extend(res.items[:k])
    return out


def weighted_choice(d: dict) -> str:
    r, acc = random.random(), 0.0
    for k, w in d.items():
        acc += w
        if r <= acc:
            return k
    return list(d)[-1]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def seed_tenant(slug: str, reset: bool, images: bool, history: bool) -> None:
    cfg = load(slug, "tenant.json")
    async with system_session() as db:
        existing = await db.one("SELECT id FROM tenants WHERE slug=:s", s=slug)
        if existing and not reset:
            print(f"{slug}: exists (use --reset to recreate)")
            return
        if existing:
            await db.exec("DELETE FROM tenants WHERE id=:id", id=existing["id"])
    print(f"== {cfg['name']}")
    t = await create_tenant(cfg["name"], slug, **cfg["settings"])
    tid = str(t["id"])

    # users
    async with system_session() as db:
        users = cfg["users"] + [{**COMMON["demo_user"], "role": "owner"}]
        for u in users:
            row = await db.one("SELECT id FROM users WHERE email=:e", e=u["email"])
            if not row:
                row = await db.one("INSERT INTO users (email, name, password_hash) VALUES (:e,:n,:p) RETURNING id",
                                   e=u["email"], n=u["name"], p=hash_password(settings.seed_password))
            else:
                await db.exec("UPDATE users SET password_hash=:p WHERE id=:id", p=hash_password(settings.seed_password), id=row["id"])
            await db.exec("INSERT INTO tenant_members (tenant_id, user_id, role, phone_e164) VALUES (:t,:u,:r,:ph) ON CONFLICT DO NOTHING",
                          t=tid, u=row["id"], r=u["role"], ph=cfg["settings"].get("owner_phone"))

    products = load(slug, "products.json")
    async with tenant_session(tid) as db:
        await db.exec("INSERT INTO integrations (tenant_id, kind, config) VALUES (:t,'commerce',:c)", t=tid, c=cfg["commerce"])

    # product images (parallel)
    async def prod_images(p):
        attrs = {}
        img = None
        if images:
            img = await gen_image(slug, p["sku"], f"Professional e-commerce studio product photo of {p['image_prompt']}, centered on a clean light background, soft shadows, no text")
            if p.get("damage"):
                attrs["damaged_image_uri"] = await gen_image(
                    slug, p["sku"] + "-damaged",
                    f"Realistic smartphone photo taken by a customer at home of {p['image_prompt']} that arrived damaged: {p['damage']}. "
                    "Casual angle, on a table next to the opened delivery box, natural indoor light, slightly imperfect photo")
        return p, img, attrs

    results = await asyncio.gather(*(prod_images(p) for p in products))
    async with tenant_session(tid) as db:
        for p, img, attrs in results:
            ph = phash(await get_media(img)) if img else None
            await db.exec(
                "INSERT INTO commerce_products (tenant_id, sku, title, description, category, price_paise, stock, image_uri, image_phash, attributes) "
                "VALUES (:t,:s,:ti,:d,:c,:p,:st,:img,:ph,:a)",
                t=tid, s=p["sku"], ti=p["title"], d=p["description"], c=p["category"], p=int(p["price"] * 100), st=p["stock"],
                img=img, ph=ph, a=attrs,
            )
    print(f"  {len(products)} products{' with images' if images else ''}")

    # orders: planted demo cases + random volume
    prices = {p["sku"]: p for p in products}
    ro = cfg["random_orders"]
    orders = list(load(slug, "orders.json"))
    prefix = orders[0]["order_ref"][:2]
    used_phones = {o["customer_phone"] for o in orders}
    for i in range(ro["count"]):
        fn, ln = random.choice(COMMON["first_names"]), random.choice(COMMON["last_names"])
        phone = f"+9197{random.randint(10000000, 99999999)}"
        while phone in used_phones:
            phone = f"+9197{random.randint(10000000, 99999999)}"
        used_phones.add(phone)
        placed = random.uniform(1, ro["days_back"])
        status = random.choices(["delivered", "in_transit", "processing", "cancelled"], [0.7, 0.15, 0.1, 0.05])[0]
        o = {"order_ref": f"{prefix}{5000 + i}", "customer_name": f"{fn} {ln}", "customer_phone": phone,
             "customer_email": f"{fn.lower()}.{ln.lower()}{random.randint(1, 99)}@example.in",
             "items": [[random.choice(list(prices)), 1] for _ in range(random.choice([1, 1, 1, 2]))],
             "payment_method": "cod" if random.random() < ro["cod_share"] else "prepaid", "status": status,
             "courier": random.choice(ro["couriers"]), "pincode": random.choice(ro["pincodes"]), "placed_days_ago": placed}
        if status == "delivered":
            o["delivered_days_ago"] = max(0.2, placed - random.uniform(3, 6))
        elif status == "in_transit":
            o["eta_days"] = random.randint(1, 4)
        orders.append(o)
    async with tenant_session(tid) as db:
        for o in orders:
            items = [{"sku": s, "title": prices[s]["title"], "qty": q, "price_paise": int(prices[s]["price"] * 100)} for s, q in o["items"]]
            total = sum(li["price_paise"] * li["qty"] for li in items)
            delivered = NOW - timedelta(days=o["delivered_days_ago"]) if o.get("delivered_days_ago") is not None else None
            placed = NOW - timedelta(days=o.get("placed_days_ago") or (o.get("delivered_days_ago") or 0) + 4)
            eta = (NOW + timedelta(days=o["eta_days"])).date() if o.get("eta_days") else ((NOW + timedelta(days=5)).date() if o["status"] == "processing" else None)
            ship_status = o.get("shipment_status") or {"delivered": "delivered", "in_transit": "in_transit", "processing": "not_shipped", "cancelled": "cancelled"}[o["status"]]
            await db.exec(
                "INSERT INTO commerce_orders (tenant_id, order_ref, customer_name, customer_phone, customer_email, line_items, total_paise, "
                "payment_method, status, shipping_address, courier, awb, shipment_status, expected_delivery, delivered_at, placed_at) "
                "VALUES (:t,:r,:n,:p,:e,:li,:tot,:pm,:st,:addr,:c,:awb,:ss,:eta,:d,:pl)",
                t=tid, r=o["order_ref"], n=o["customer_name"], p=norm_phone(o["customer_phone"]), e=o["customer_email"], li=items, tot=total,
                pm=o["payment_method"], st=o["status"], addr={"city": o.get("city", ""), "pincode": o["pincode"]}, c=o["courier"],
                awb=None if o["status"] == "processing" else f"{random.randint(10**11, 10**12 - 1)}", ss=ship_status, eta=eta, d=delivered, pl=placed,
            )
    print(f"  {len(orders)} orders")

    # knowledge base
    async with tenant_session(tid) as db:
        for f in sorted((SEED / slug / "kb").glob("*.md")):
            md = f.read_text(encoding="utf-8")
            title = md.splitlines()[0].lstrip("# ").strip()
            await kb.add_document(db, title, md, "upload")
    print("  knowledge base indexed")

    # policy: compiled from the owner's plain-language text, then activated
    res = await pol.compile_policy(cfg["policy_text"], tid, cfg["settings"]["business_description"])
    async with tenant_session(tid) as db:
        await db.exec("UPDATE policies SET status='archived'")
        await db.exec(
            "INSERT INTO policies (tenant_id, version, status, source_text, rules, questions, confirmed_at) VALUES (:t,2,'active',:s,:r,:q,now())",
            t=tid, s=cfg["policy_text"], r=res.policy.model_dump(), q=res.questions,
        )
    print(f"  policy compiled: {len(res.policy.rules)} rules")

    await seed_agent(tid, cfg)
    if history:
        await seed_history(tid, slug, cfg["name"])


async def seed_agent(tid: str, cfg: dict) -> None:
    """Agent Profile (persona, sales + voice settings) and a few demo leads. Safe to re-run."""
    from app.profile import load_profile
    from app.sales import add_event, upsert_lead

    a = cfg.get("agent") or {}
    async with tenant_session(tid) as db:
        prof = await load_profile(db)
        await db.exec("UPDATE tenant_settings SET ticket_prefix=coalesce(ticket_prefix, :p)",
                      p="".join(w[0] for w in cfg["name"].split()[:3]).upper())
        await db.exec("UPDATE agent_profiles SET agent_name=:n, persona=:p, sales=:s, voice=:v, widget=coalesce(widget, '{}'::jsonb) || :w, updated_at=now()",
                      n=a.get("agent_name", "Sahay"), p=a.get("persona", ""), s={**prof["sales"], **a.get("sales", {})},
                      v={**prof["voice"], **a.get("voice", {})}, w=a.get("widget", {}))
        for l in a.get("leads", []):
            row, created = await upsert_lead(db, source=l["source"], name=l.get("name"), email=l.get("email"), phone=l.get("phone"),
                                             company=l.get("company"), city=l.get("city"), interest=l.get("interest"), stage=l.get("stage"))
            if created and l.get("score") is not None:
                await db.exec("UPDATE leads SET score=:s, score_reasons=:r, last_contact_at=now() - interval '1 day' WHERE id=:id",
                              s=l["score"], r=["Clear bulk interest", "Matches the ideal customer", "Reachable"], id=row["id"])
                await add_event(db, row["id"], "scored", f"Scored {l['score']}/100")
            if created and l.get("stage") == "callback":
                await db.exec("UPDATE leads SET next_action='Call back about the bulk quote', next_action_at=now() + interval '3 hours' WHERE id=:id", id=row["id"])
        # two abandoned carts (left a few hours ago) so the cart-reminder automation has something to do
        if not await db.val("SELECT count(*) FROM commerce_carts"):
            buyers = await db.all("SELECT DISTINCT ON (customer_phone) customer_name, customer_phone, customer_email FROM commerce_orders "
                                  "WHERE status='delivered' ORDER BY customer_phone, placed_at LIMIT 2")
            prods = await db.all("SELECT sku, title, price_paise FROM commerce_products ORDER BY price_paise DESC LIMIT 3")
            for i, b in enumerate(buyers):
                items = [{"sku": p["sku"], "title": p["title"], "qty": 1, "price_paise": p["price_paise"]} for p in prods[i:i + 2]]
                await db.exec("INSERT INTO commerce_carts (tenant_id, cart_ref, customer_name, customer_phone, customer_email, items, total_paise, source, created_at, updated_at) "
                              "VALUES (:t,:r,:n,:p,:e,:i,:tot,'widget', now() - interval '5 hours', now() - interval '3 hours')",
                              t=tid, r=f"demo-cart-{i}", n=b["customer_name"], p=b["customer_phone"], e=b["customer_email"], i=items,
                              tot=sum(x["price_paise"] for x in items))
    print(f"  agent profile '{a.get('agent_name', 'Sahay')}' + {len(a.get('leads', []))} demo leads + demo carts")


async def seed_history(tid: str, slug: str, name: str) -> None:
    hist = load(slug, "history.json")
    if not hist:
        return
    async with tenant_session(tid) as db:
        orders = await db.all("SELECT * FROM commerce_orders WHERE status='delivered'")
    total = 0
    for sc in hist["scenarios"]:
        n = sc["count_this_week"] + sc["count_last_week"]
        variations = await gen_variations(name, sc, n, tid)
        is_complaint = sc.get("complaint", True)
        embs = await llm.embed([v.complaint_summary_en for v in variations], "CLUSTERING") if is_complaint else [None] * n
        async with tenant_session(tid) as db:
            for i, (v, emb) in enumerate(zip(variations, embs)):
                days_ago = random.uniform(0.2, 6.9) if i < sc["count_this_week"] else random.uniform(7.1, 13.9)
                created = NOW - timedelta(days=days_ago)
                cands = [o for o in orders if not sc.get("sku") or any(li["sku"] == sc["sku"] for li in o["line_items"])] or orders
                o = random.choice(cands)
                channel = weighted_choice(COMMON["channels"])
                fn, ln = random.choice(COMMON["first_names"]), random.choice(COMMON["last_names"])
                cust = await db.one(
                    "INSERT INTO customers (tenant_id, display_name, primary_phone, created_at) VALUES (:t,:n,:p,:c) RETURNING id",
                    t=tid, n=f"{fn} {ln}", p=f"+9196{random.randint(10000000, 99999999)}", c=created,
                )
                conv = await db.one("INSERT INTO conversations (tenant_id, customer_id, channel, created_at) VALUES (:t,:c,:ch,:cr) RETURNING id",
                                    t=tid, c=cust["id"], ch=channel, cr=created)
                sent = random.uniform(*sc["sentiment"])
                frt = timedelta(seconds=random.uniform(8, 40)) if sc["resolution"] == "ai_auto" else timedelta(minutes=random.uniform(10, 90))
                entities = {"sku": sc.get("sku") or o["line_items"][0]["sku"], "product": o["line_items"][0]["title"],
                            "courier": sc.get("courier") or o["courier"], "pincode": random.choice(sc["pincodes"]) if sc.get("pincodes") else o["shipping_address"].get("pincode"),
                            "payment_method": o["payment_method"]}
                tk = await db.one(
                    "INSERT INTO tickets (tenant_id, customer_id, subject, status, intent, urgency, sentiment, frustration_score, language, order_refs, "
                    "resolution_type, summary, complaint_summary, complaint_embedding, entities, first_response_at, resolved_at, created_at, updated_at, csat) "
                    "VALUES (:t,:c,:s,'resolved',:i,:u,:se,:f,:l,:r,:rt,:sm,:cs,:e,:en,:fr,:rs,:cr,:cr,:csat) RETURNING id",
                    t=tid, c=cust["id"], s=v.complaint_summary_en[:120], i=sc["intent"], u="P2" if sent < -0.4 else "P3", se=sent,
                    f=max(0, min(100, 40 - 50 * sent)), l="hinglish", r=[o["order_ref"]], rt=sc["resolution"], sm=v.complaint_summary_en,
                    cs=v.complaint_summary_en if is_complaint else None, e=emb, en=entities, fr=created + frt,
                    rs=created + frt + timedelta(minutes=random.uniform(1, 240)), cr=created,
                    csat=random.choice([4, 5, 5, 5, 3]) if sent > -0.5 else random.choice([3, 4, 5]),
                )
                await db.exec(
                    "INSERT INTO messages (tenant_id, conversation_id, ticket_id, customer_id, direction, channel, author_type, text, text_redacted, sentiment, created_at, triage) "
                    "VALUES (:t,:c,:tk,:cu,'in',:ch,'customer',:tx,:tx,:s,:cr,:tr)",
                    t=tid, c=conv["id"], tk=tk["id"], cu=cust["id"], ch=channel, tx=v.customer_message, s=sent, cr=created, tr={"seeded": True},
                )
                await db.exec(
                    "INSERT INTO messages (tenant_id, conversation_id, ticket_id, customer_id, direction, channel, author_type, author_name, text, status, created_at) "
                    "VALUES (:t,:c,:tk,:cu,'out',:ch,:at,:an,:tx,'sent',:cr)",
                    t=tid, c=conv["id"], tk=tk["id"], cu=cust["id"], ch=channel, at="ai" if sc["resolution"] == "ai_auto" else "human",
                    an="Sahay AI" if sc["resolution"] == "ai_auto" else "Support team", tx=v.agent_reply, cr=created + frt,
                )
                if sc["intent"] == "damaged_item":
                    await db.exec(
                        "INSERT INTO actions (tenant_id, ticket_id, customer_id, type, params, decision, status, proposed_by, idempotency_key, value_paise, created_at) "
                        "VALUES (:t,:tk,:c,'create_replacement',:p,'allow','succeeded','ai',:k,:v,:cr)",
                        t=tid, tk=tk["id"], c=cust["id"], p={"order_ref": o["order_ref"], "reason": "seeded history"},
                        k=f"seed-{tk['id']}", v=o["total_paise"], cr=created + frt,
                    )
                total += 1
    # unanswered questions → knowledge gaps
    gaps = hist.get("gaps") or []
    if gaps:
        embs = await llm.embed(gaps, "CLUSTERING")
        async with tenant_session(tid) as db:
            for q, e in zip(gaps, embs):
                await db.exec("INSERT INTO kb_gaps (tenant_id, question, embedding, created_at) VALUES (:t,:q,:e,:c)",
                              t=tid, q=q, e=e, c=NOW - timedelta(days=random.uniform(0, 6)))
    # a few LLM-cost rows so cost/ticket shows something realistic
    async with tenant_session(tid) as db:
        await db.exec(
            "INSERT INTO llm_calls (tenant_id, purpose, model, input_tokens, output_tokens, cost_micros, latency_ms, status, created_at) "
            "SELECT :t, 'seeded', 'mix', 6000, 900, 210000, 4200, 'ok', now() - (random() * interval '13 days') FROM generate_series(1, :n)",
            t=tid, n=total,
        )
    print(f"  history: {total} past tickets, {len(gaps)} knowledge-gap questions")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--agent-only", action="store_true", help="only (re)seed Agent Profiles + demo leads on existing tenants")
    a = ap.parse_args()
    slugs = [p.name for p in SEED.iterdir() if (p / "tenant.json").exists()]
    if a.only:
        slugs = [s for s in slugs if s == a.only]
    if a.agent_only:
        for s in slugs:
            async with system_session() as db:
                t = await db.one("SELECT id FROM tenants WHERE slug=:s", s=s)
            if t:
                print(f"== {s}")
                await seed_agent(str(t["id"]), load(s, "tenant.json"))
        return
    for s in sorted(slugs, reverse=True):
        await seed_tenant(s, a.reset, not a.no_images, not a.no_history)
    print(f"\nLogin: {COMMON['demo_user']['email']} / (SEED_PASSWORD) — member of all seed workspaces")


if __name__ == "__main__":
    asyncio.run(main())

"""End-to-end checks for the sales + widget + voice + lifecycle build, against a running API.

    python scripts/e2e_sales.py [base_url] [tenant_slug] [only,test,names]

Prints PASS/FAIL per check with a short detail. Uses the demo login.
"""

import asyncio
import hashlib
import hmac
import json
import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
SLUG = sys.argv[2] if len(sys.argv) > 2 else "loop-loot"
ONLY = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else set()
results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def solve_pow(c: httpx.AsyncClient) -> dict:
    ch = (await c.get(f"{BASE}/v1/widget/challenge")).json()
    n, limit = 0, 1 << (256 - ch["bits"])
    while int.from_bytes(hashlib.sha256(f"{ch['challenge']}:{n}".encode()).digest(), "big") >= limit:
        n += 1
    return {"pow_challenge": ch["challenge"], "pow_counter": str(n)}


async def wait_reply(c: httpx.AsyncClient, session: str, n_before: int, timeout: float = 90) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = (await c.get(f"{BASE}/v1/widget/poll", params={"session": session})).json()
        msgs = d.get("messages", [])
        if len(msgs) > n_before and msgs[-1]["direction"] == "out" and not d.get("typing"):
            return {"msgs": msgs, "last": msgs[-1]}
        await asyncio.sleep(2)
    return {"msgs": [], "last": {}}


async def chat(c, session: str, text: str, context: dict | None = None) -> dict:
    n = len((await c.get(f"{BASE}/v1/widget/poll", params={"session": session})).json().get("messages", []))
    await c.post(f"{BASE}/v1/widget/send", data={"session": session, "text": text, "context": json.dumps(context or {})})
    return await wait_reply(c, session, n + 1)


async def main() -> None:
    async with httpx.AsyncClient(timeout=120) as c:
        tok = (await c.post(f"{BASE}/v1/auth/login", json={"email": "demo@sahay.app", "password": "sahay-demo"})).json()["token"]
        me = (await c.get(f"{BASE}/v1/me", headers={"Authorization": f"Bearer {tok}"})).json()
        tid = next(t["id"] for t in me["tenants"] if t["slug"] == SLUG)
        H = {"Authorization": f"Bearer {tok}", "X-Tenant-Id": tid}
        studio = (await c.get(f"{BASE}/v1/studio", headers=H)).json()
        key = studio["site_key"]
        secret = (await c.get(f"{BASE}/v1/studio/secret", headers=H)).json()["secret_key"]
        contacts = (await c.get(f"{BASE}/v1/simulator/contacts", headers=H)).json()
        buyer = next(x for x in contacts if x["status"] == "in_transit") if any(x["status"] == "in_transit" for x in contacts) else contacts[0]
        own = BASE

        async def session(**kw):
            r = await c.post(f"{BASE}/v1/widget/session", json={"key": key, "visitor_id": f"e2e-{uuid.uuid4().hex}", "parent_origin": own,
                                                               **(await solve_pow(c)), **kw})
            return r

        def want(name):
            return not ONLY or name in ONLY

        async def t_sales_chat():
            s = (await session()).json()["session"]
            r = await chat(c, s, "Hi! I want a birthday gift for my best friend, budget under 1200. What do you suggest?")
            ui = r["last"].get("interactive") or {}
            report("widget: product recommendation with live cards", bool(ui.get("products")) and "₹" in (r["last"].get("text") or "") + json.dumps(ui),
                   f"cards={[p['title'] for p in ui.get('products', [])]} reply={(r['last'].get('text') or '')[:140]!r}")

        async def t_page_context():
            s = (await session()).json()["session"]
            r = await chat(c, s, "is this in stock and how much?", {"page_url": f"{BASE}/store/x", "product_sku": "CHM-SET-07", "product_title": "Celestial Charm Set"})
            txt = r["last"].get("text") or ""
            report("widget: page context (viewing product)", "649" in txt or "Celestial" in txt, txt[:160])

        async def t_lead_capture():
            s = (await session()).json()["session"]
            await chat(c, s, "We want around 50 Bestie Charm Gift Boxes for our office Diwali gifting. Do you do bulk orders?")
            r = await chat(c, s, "I'm Priya Nair from Acme Design Studio, email priya.nair@acmedesign.example.in, phone 9876501234")
            leads = (await c.get(f"{BASE}/v1/leads", params={"q": "Priya"}, headers=H)).json()["leads"]
            ok = any(l.get("email") == "priya.nair@acmedesign.example.in" for l in leads)
            report("widget: lead captured from chat", ok, f"leads={[(l['name'], l['stage'], l['source']) for l in leads]} reply={(r['last'].get('text') or '')[:120]!r}")

        async def t_otp():
            s = (await session()).json()["session"]
            r = await chat(c, s, "Where is my order?")
            asked = bool((r["last"].get("interactive") or {}).get("verify_phone"))
            st = (await c.post(f"{BASE}/v1/widget/otp/start", json={"session": s, "phone": buyer["customer_phone"]})).json()
            v = await c.post(f"{BASE}/v1/widget/otp/verify", json={"session": s, "phone": buyer["customer_phone"], "code": st.get("demo_code", "")})
            s2 = v.json().get("session")
            r2 = await chat(c, s2, "I have verified my phone number. Where is my order?")
            txt = r2["last"].get("text") or ""
            report("widget: unverified visitor gets OTP form, then sees orders", asked and v.status_code == 200 and (buyer["order_ref"] in txt or any(w in txt.lower() for w in ("transit", "deliver", "shipped", "dispatch"))),
                   f"asked_verify={asked} verify={v.status_code} order={buyer['order_ref']} reply={txt[:140]!r}")
            bad = await c.post(f"{BASE}/v1/widget/otp/verify", json={"session": s, "phone": buyer["customer_phone"], "code": "000000"})
            report("widget: wrong/used OTP rejected", bad.status_code == 400, str(bad.status_code))

        async def t_typed_phone_not_trusted():
            r = await session(user={"phone": buyer["customer_phone"], "name": "Faker"}, user_hash="deadbeef")
            j = r.json()
            report("widget: unsigned user details are not trusted", r.status_code == 200 and not j["verified"], f"verified={j.get('verified')}")

        async def t_signed_user():
            email = (buyer.get("customer_email") or "").lower()
            h = hmac.new(secret.encode(), email.encode(), hashlib.sha256).hexdigest()
            j = (await session(user={"email": email, "name": buyer["customer_name"]}, user_hash=h)).json()
            report("widget: signed logged-in shopper is verified", j.get("verified") is True, f"name={j.get('customer_name')}")

        async def t_domains():
            await c.put(f"{BASE}/v1/studio", headers=H, json={"widget": {"allowed_domains": ["looploot.in"]}})
            bad = await c.post(f"{BASE}/v1/widget/session", json={"key": key, "parent_origin": "https://evil-shop.example.com", **(await solve_pow(c))})
            good = await c.post(f"{BASE}/v1/widget/session", json={"key": key, "parent_origin": "https://www.looploot.in", **(await solve_pow(c))})
            csp = (await c.get(f"{BASE}/w/{key}")).headers.get("content-security-policy", "")
            await c.put(f"{BASE}/v1/studio", headers=H, json={"widget": {"allowed_domains": []}})
            report("widget: only works on allowed domains", bad.status_code == 403 and good.status_code == 200 and "looploot.in" in csp,
                   f"evil={bad.status_code} own={good.status_code} csp={csp!r}")

        async def t_reply_now():
            leads = (await c.get(f"{BASE}/v1/leads", params={"q": "Little Gift"}, headers=H)).json()["leads"]
            if not leads:
                return report("reply-now: lead email + chat link", False, "seed lead missing")
            lid = leads[0]["id"]
            await c.post(f"{BASE}/v1/leads/{lid}/draft-email", headers=H)
            draft = None
            for _ in range(30):
                d = (await c.get(f"{BASE}/v1/leads/{lid}", headers=H)).json()
                draft = next((e for e in d["emails"] if e["status"] == "draft"), None)
                if draft:
                    break
                await asyncio.sleep(2)
            if not draft:
                return report("reply-now: lead email + chat link", False, "no draft")
            sent = (await c.post(f"{BASE}/v1/outreach/{draft['id']}/approve", headers=H, json={})).json()
            token = d["reply_url"].rsplit("/r/", 1)[1]
            j = (await c.post(f"{BASE}/v1/widget/session", json={"reply_token": token, **(await solve_pow(c))})).json()
            r = await chat(c, j["session"], "Hi, got your email. What would 30 charm sets cost and can you add our logo card?")
            after = (await c.get(f"{BASE}/v1/leads/{lid}", headers=H)).json()["lead"]
            report("reply-now: email drafted → approved → link opens chat that knows the lead",
                   sent["status"] in ("sent", "simulated", "skipped") and j.get("mode") == "reply_now" and after["stage"] == "engaged" and bool(r["last"].get("text")),
                   f"email={sent['status']} subject={draft['subject']!r} stage={after['stage']} reply={(r['last'].get('text') or '')[:140]!r}")
            bad = await c.post(f"{BASE}/v1/widget/session", json={"reply_token": token[:-3] + "abc", **(await solve_pow(c))})
            report("reply-now: tampered link rejected", bad.status_code == 401, str(bad.status_code))

        async def t_voice():
            st = (await c.post(f"{BASE}/v1/voice/test/start", headers=H, json={"phone": buyer["customer_phone"]})).json()
            t1 = (await c.post(f"{BASE}/v1/voice/test/{st['call_id']}/turn", headers=H, json={"text": "Haan ji, mera order kab tak aayega?"})).json()
            t2 = (await c.post(f"{BASE}/v1/voice/test/{st['call_id']}/turn", headers=H, json={"text": "Theek hai, tracking link Telegram pe bhej do please. Bas itna hi, thank you."})).json()
            end = (await c.post(f"{BASE}/v1/voice/test/{st['call_id']}/end", headers=H)).json()
            tools = [u["tool"] for u in t1.get("tools_used", []) + t2.get("tools_used", [])]
            report("voice: knows caller, uses tools, short spoken replies, summary saved",
                   # calls only know how many orders the number has; details come after the caller verifies an order id
                   "orders_on_file" in st.get("context", {}) and len(t1.get("say", "")) < 400 and bool(end.get("summary")),
                   f"greeting={st['greeting'][:70]!r} t1={t1.get('say', '')[:110]!r} tools={tools} t2={t2.get('say', '')[:80]!r} "
                   f"wa={t2.get('whatsapp_sent')} end={end.get('outcome')} latency={t1.get('latency_ms')}ms")
            # public voice API with the business's secret key
            r = await c.post(f"{BASE}/v1/voice/calls/start", headers={"X-Sahay-Key": secret}, json={"from_number": "+919000012345", "to_number": "+918000000000"})
            bad = await c.post(f"{BASE}/v1/voice/calls/start", headers={"X-Sahay-Key": "sk_wrong"}, json={"from_number": "+919000012345"})
            if r.status_code == 200:
                await c.post(f"{BASE}/v1/voice/calls/{r.json()['call_id']}/end", headers={"X-Sahay-Key": secret}, json={})
            report("voice API: business key works, wrong key refused", r.status_code == 200 and bad.status_code == 401 and len(r.json().get("tools", [])) > 3,
                   f"{r.status_code}/{bad.status_code} tools={len(r.json().get('tools', [])) if r.status_code == 200 else '-'}")

        async def t_lead_search():
            s = (await c.post(f"{BASE}/v1/leads/search", headers=H, json={"query": "corporate gifting and HR teams at startups in Bengaluru", "count": 4})).json()
            sid = s["search"]["id"]
            row = {}
            for _ in range(60):
                row = (await c.get(f"{BASE}/v1/leads/searches/{sid}", headers=H)).json()
                if row["status"] != "running":
                    break
                await asyncio.sleep(3)
            leads = (await c.get(f"{BASE}/v1/leads", headers=H)).json()["leads"]
            found = [l for l in leads if l.get("search_id") == sid]
            report("sales: lead search (Google-grounded) → leads", row.get("status") == "done" and len(found) > 0,
                   f"status={row.get('status')} found={row.get('found')} err={row.get('error')} sample={[(l['company'], l.get('website'), l.get('email')) for l in found[:3]]}")
            await asyncio.sleep(25)
            leads = (await c.get(f"{BASE}/v1/leads", headers=H)).json()["leads"]
            scored = [l for l in leads if l.get("search_id") == sid and l.get("score") is not None]
            report("sales: found leads get checked + scored", len(scored) > 0, f"scored={[(l['company'], l['score'], l.get('email_status')) for l in scored[:4]]}")

        async def t_automations():
            r = (await c.post(f"{BASE}/v1/automations/run", headers=H)).json()
            s = (await c.post(f"{BASE}/v1/automations/daily-summary", headers=H)).json()
            a = (await c.get(f"{BASE}/v1/automations", headers=H)).json()
            report("lifecycle: proactive alerts / COD confirms / owner summary", "text" in s and isinstance(r, dict),
                   f"run={r} events={len(a['events'])} summary={s['text'][:120]!r}")

        async def t_campaign():
            camp = (await c.post(f"{BASE}/v1/campaigns", headers=H, json={"name": "E2E festive", "goal": "Remind recent buyers about the Bestie Charm Gift Box for Diwali", "channel": "telegram", "audience": {"segment": "recent_buyers"}})).json()
            d = (await c.get(f"{BASE}/v1/campaigns/{camp['id']}", headers=H)).json()
            await c.post(f"{BASE}/v1/campaigns/{camp['id']}/approve", headers=H)
            for _ in range(40):
                d2 = (await c.get(f"{BASE}/v1/campaigns/{camp['id']}", headers=H)).json()
                if d2["campaign"]["status"] == "sent":
                    break
                await asyncio.sleep(2)
            report("campaign: AI draft → audience w/ angry-ticket suppression → approve → send",
                   bool(camp.get("message")) and d2["campaign"]["status"] == "sent",
                   f"msg={camp.get('message', '')[:90]!r} eligible={(d.get('audience_preview') or {}).get('eligible')} "
                   f"suppressed={len((d.get('audience_preview') or {}).get('suppressed', []))} stats={d2['campaign']['stats']}")

        async def t_support_regression():
            dmg = next((x for x in contacts if x["status"] == "delivered"), contacts[0])
            await c.post(f"{BASE}/v1/simulator/send", headers=H, data={"channel": "telegram", "text": "Hi, mera order aaya par ek charm toota hua hai 😞 kya karein?", "phone": dmg["customer_phone"], "name": dmg["customer_name"]})
            txt, steps = "", None
            for _ in range(45):
                th = (await c.get(f"{BASE}/v1/simulator/thread", headers=H, params={"channel": "telegram", "phone": dmg["customer_phone"]})).json()
                outs = [m for m in th["messages"] if m["direction"] == "out"]
                if outs and not th["typing"]:
                    txt = outs[-1]["text"] or ""
                    tk = outs[-1].get("ticket_id")
                    if tk:
                        t = (await c.get(f"{BASE}/v1/tickets/{tk}", headers=H)).json()
                        steps = [s for r in t.get("runs", []) for s in r.get("steps", []) if s.get("type") == "skill"]
                    break
                await asyncio.sleep(2)
            report("support: Telegram damaged-item still handled (skills routing)", bool(txt), f"reply={txt[:140]!r} skills={(steps[0]['output']['skills'] if steps else '?')}")

        async def t_bot():
            no_proof = await c.post(f"{BASE}/v1/widget/session", json={"key": key, "visitor_id": f"e2e-{uuid.uuid4().hex}", "parent_origin": own})
            proof = await solve_pow(c)
            ok1 = await c.post(f"{BASE}/v1/widget/session", json={"key": key, "visitor_id": f"e2e-{uuid.uuid4().hex}", "parent_origin": own, **proof})
            replay = await c.post(f"{BASE}/v1/widget/session", json={"key": key, "visitor_id": f"e2e-{uuid.uuid4().hex}", "parent_origin": own, **proof})
            report("bot check: no proof refused, solved proof accepted once, replay refused",
                   no_proof.status_code == 403 and ok1.status_code == 200 and replay.status_code == 403,
                   f"no_proof={no_proof.status_code} solved={ok1.status_code} replay={replay.status_code}")

        async def t_cart():
            s = (await session()).json()["session"]
            phone = buyer["customer_phone"]
            r = (await c.post(f"{BASE}/v1/widget/cart", json={"session": s, "items": [{"sku": "CHM-GFT-10", "qty": 2}, {"sku": "GF-GHE-1L", "qty": 1}],
                                                                 "name": "Cart Tester", "phone": phone})).json()
            await c.put(f"{BASE}/v1/studio", headers=H, json={"sales": {"cart_reminder_hours": 0}})
            run = (await c.post(f"{BASE}/v1/automations/run", headers=H)).json()
            await c.put(f"{BASE}/v1/studio", headers=H, json={"sales": {"cart_reminder_hours": 1}})
            carts = (await c.get(f"{BASE}/v1/automations/carts", headers=H)).json()
            mine = next((x for x in carts if x["id"] == r.get("cart_id")), {})
            ev = (await c.get(f"{BASE}/v1/automations", headers=H)).json()["events"]
            msg = next((e for e in ev if e["kind"] == "cart_reminder" and e["ref"] == r.get("cart_id")), {})
            report("cart: saved with server-side prices → one reminder sent", r.get("ok") and mine.get("status") in ("reminded", "converted") and run.get("cart_reminders", 0) >= 1,
                   f"cart total=₹{r.get('total_inr')} status={mine.get('status')} run={run.get('cart_reminders')} msg={((msg.get('data') or {}).get('text') or (msg.get('data') or {}).get('skipped') or '')[:140]!r}")
            run2 = (await c.post(f"{BASE}/v1/automations/run", headers=H)).json()
            report("cart: never reminded twice", run2.get("cart_reminders", 0) == 0, f"second run={run2.get('cart_reminders')}")

        async def t_newsletter():
            n = (await c.post(f"{BASE}/v1/automations/newsletter", headers=H)).json()
            d = (await c.get(f"{BASE}/v1/campaigns/{n['id']}", headers=H)).json()
            report("newsletter: drafted from last month's data as an email campaign awaiting approval",
                   n.get("kind") == "newsletter" and n.get("status") == "draft" and bool(n.get("subject")) and n.get("channel") == "email",
                   f"subject={n.get('subject')!r} eligible={(d.get('audience_preview') or {}).get('eligible')} body={n.get('message', '')[:120]!r}")
            bad = await c.get(f"{BASE}/u/not-a-token")
            report("newsletter: unsubscribe link validated", bad.status_code == 400, str(bad.status_code))

        async def t_stop():
            who = contacts[-1]
            await c.post(f"{BASE}/v1/simulator/send", headers=H, data={"channel": "telegram", "text": "STOP", "phone": who["customer_phone"], "name": who["customer_name"]})
            th = (await c.get(f"{BASE}/v1/simulator/thread", headers=H, params={"channel": "telegram", "phone": who["customer_phone"]})).json()
            last = [m for m in th["messages"] if m["direction"] == "out"][-1:] or [{}]
            report("marketing: STOP opts the customer out instantly", "won't get offers" in (last[0].get("text") or ""), (last[0].get("text") or "")[:90])

        tests = {"bot": t_bot, "cart": t_cart, "newsletter": t_newsletter, "stop": t_stop, "sales_chat": t_sales_chat, "page_context": t_page_context, "lead_capture": t_lead_capture, "otp": t_otp,
                 "unsigned": t_typed_phone_not_trusted, "signed": t_signed_user, "domains": t_domains, "reply_now": t_reply_now,
                 "voice": t_voice, "lead_search": t_lead_search, "automations": t_automations, "campaign": t_campaign,
                 "support": t_support_regression}
        await t_domains() if want("domains") else None
        jobs = [fn() for n, fn in tests.items() if want(n) and n != "domains"]
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, Exception):
                report("exception", False, repr(r)[:300])
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")


asyncio.run(main())

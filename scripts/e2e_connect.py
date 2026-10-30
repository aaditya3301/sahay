"""End-to-end check of "a business with its own website" (the UrbanKicks story) against a running local API.

    cd apps/api && uv run python ../../scripts/e2e_connect.py http://localhost:8080

Creates a fresh workspace, starts scripts/mock_site.py (Commerce Contract via the Python SDK + webhook receiver),
then: key ping → policy → events → connect custom endpoint → health → signed widget chat asking for an exchange
→ the AI calls createReturn on "their site" → ticket with the business prefix → webhook delivered and verified
→ exchange.shipped event joins the ticket → a later angry email reopens the same ticket → Sarvam hooks/tools/webhook.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "python-sdk"))
from sahay import Sahay  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
LOG = ROOT / "scripts" / "mock_site_log.json"
SECRET_FILE = ROOT / "scripts" / ".webhook_secret"
results = []


def report(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def site_log(kind=None):
    data = json.loads(LOG.read_text()) if LOG.exists() else []
    return [d for d in data if not kind or d["kind"] == kind]


async def solve_pow(c):
    ch = (await c.get(f"{BASE}/v1/widget/challenge")).json()
    n, lim = 0, 1 << (256 - ch["bits"])
    while int.from_bytes(hashlib.sha256(f"{ch['challenge']}:{n}".encode()).digest(), "big") >= lim:
        n += 1
    return {"pow_challenge": ch["challenge"], "pow_counter": str(n)}


async def until(fn, timeout=90, every=2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = await fn()
        if v:
            return v
        await asyncio.sleep(every)
    return None


async def main():
    LOG.unlink(missing_ok=True)
    SECRET_FILE.unlink(missing_ok=True)
    async with httpx.AsyncClient(timeout=120) as c:
        email = f"owner+{uuid.uuid4().hex[:6]}@urbankicks.test"
        su = (await c.post(f"{BASE}/v1/auth/signup", json={"name": "Asha", "email": email, "password": "test-pass-123", "workspace": "UrbanKicks",
                                                            "website_url": "https://urbankicks.example.in",
                                                            "business_description": "UrbanKicks sells running and casual shoes online across India."})).json()
        tid = su["tenants"][0]["id"]
        H = {"Authorization": f"Bearer {su['token']}", "X-Tenant-Id": tid}
        me = (await c.get(f"{BASE}/v1/me", headers=H)).json()
        studio = (await c.get(f"{BASE}/v1/studio", headers=H)).json()
        secret = (await c.get(f"{BASE}/v1/studio/secret", headers=H)).json()["secret_key"]
        report("signup: new business gets keys + its own ticket prefix", secret.startswith("sk_") and me["ticket_prefix"] == "U",
               f"prefix={me['ticket_prefix']} site_key={studio['site_key'][:14]}…")
        await c.patch(f"{BASE}/v1/settings", headers=H, json={"default_autonomy": "auto", "ticket_prefix": "UK"})
        site = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mock_site.py"), "8090", "8091", str(LOG)],
                                env={**os.environ, "SAHAY_SECRET_KEY": secret, "WEBHOOK_SECRET_FILE": str(SECRET_FILE)},
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            await asyncio.sleep(1.5)
            sdk = Sahay(secret, host=BASE)
            ping = (await c.get(f"{BASE}/v1/api/ping", headers={"Authorization": f"Bearer {secret}"})).json()
            bad = await c.get(f"{BASE}/v1/api/ping", headers={"Authorization": "Bearer sk_nope"})
            report("REST API: secret key identifies the business, wrong key refused", ping["business"]["name"] == "UrbanKicks" and bad.status_code == 401)

            pol = (await c.post(f"{BASE}/v1/policies/compile", headers=H, json={"text": "Size exchanges within 7 days of delivery can be done automatically. "
                                "Refunds always need my approval. Never cancel shipped orders."})).json()
            await c.post(f"{BASE}/v1/policies/{pol['id']}/activate", headers=H)

            await asyncio.to_thread(sdk.send_event, "product.updated", {"sku": "RUN-9-09", "title": "Runner 9 (UK 9)", "price": 2499, "stock": 6})
            ev = await asyncio.to_thread(sdk.send_event, "order.created", {**{k: v for k, v in json.loads(json.dumps({
                "orderRef": "5531", "customerName": "Rahul Verma", "customerPhone": "+919812345678", "customerEmail": "rahul.verma@example.in",
                "items": [{"sku": "RUN-9-08", "title": "Runner 9 (UK 8)", "qty": 1, "price": 2499}], "total": 2499, "paymentMethod": "prepaid",
                "status": "delivered"})).items()}}, id="evt-order-5531")
            dup = await asyncio.to_thread(sdk.send_event, "order.created", {"orderRef": "5531"}, id="evt-order-5531")
            report("events: order + product saved, repeated event ignored", ev.get("order") == "5531" and dup.get("duplicate") is True, f"{ev} / {dup}")

            await c.put(f"{BASE}/v1/integrations/commerce", headers=H, json={"provider": "custom", "endpoint_url": "http://127.0.0.1:8090/sahay"})
            health = (await c.get(f"{BASE}/v1/integrations/health", headers=H)).json()["checks"]
            store = next(x for x in health if x["key"] == "store")
            report("Integration Health: signed ping to their endpoint is green", store["status"] == "green", store["detail"])

            wh = (await c.post(f"{BASE}/v1/webhooks", headers=H, json={"url": "http://127.0.0.1:8091/hooks", "events": ["*"]})).json()
            SECRET_FILE.write_text(wh["secret"])

            signed = sdk.identify(email="rahul.verma@example.in", name="Rahul Verma")
            sess = (await c.post(f"{BASE}/v1/widget/session", json={"key": studio["site_key"], "visitor_id": f"e2e-{uuid.uuid4().hex}",
                                                                      "parent_origin": BASE, "user": signed["user"], "user_hash": signed["userHash"],
                                                                      **(await solve_pow(c))})).json()
            await c.post(f"{BASE}/v1/widget/send", data={"session": sess["session"], "text": "size galat aa gaya, exchange karna hai. UK 9 chahiye", "context": "{}"})

            async def reply():
                d = (await c.get(f"{BASE}/v1/widget/poll", params={"session": sess["session"]})).json()
                outs = [m for m in d["messages"] if m["direction"] == "out"]
                return outs[-1] if outs and not d["typing"] else None
            r = await until(reply, 120)
            calls = [x["action"] for x in site_log("contract")]
            report("widget → AI looked up the order on THEIR site and proposed the exchange",
                   "findOrders" in calls or "getOrder" in calls, f"site calls={calls} reply={(r or {}).get('text', '')[:160]!r}")
            if "createReturn" not in calls:  # the business's own rules held it for approval: the owner approves
                aps = (await c.get(f"{BASE}/v1/approvals", headers=H)).json()
                aps = aps if isinstance(aps, list) else aps.get("approvals", aps.get("items", []))
                pend = [a for a in aps if a.get("kind") == "action"]
                for a in pend:
                    await c.post(f"{BASE}/v1/approvals/{a['id']}/decide", headers=H, json={"decision": "approve"})
                done = await until(lambda: asyncio.sleep(0, "createReturn" in [x["action"] for x in site_log("contract")]), 30)
                report("owner approved → exchange created on THEIR site via the Commerce Contract", bool(done),
                       f"approvals={len(pend)} site calls={[x['action'] for x in site_log('contract')]}")
            else:
                report("exchange created on THEIR site via the Commerce Contract", True, "allowed by policy without approval")

            tickets = (await c.get(f"{BASE}/v1/api/tickets", params={"email": "rahul.verma@example.in"}, headers={"Authorization": f"Bearer {secret}"})).json()
            report("ticket lives in Sahay with the business numbering", bool(tickets) and tickets[0]["ref"].startswith("UK-"),
                   f"{[(t['ref'], t['status'], t['intent']) for t in tickets]}")

            hooks = await until(lambda: asyncio.sleep(0, [h for h in site_log("webhook") if h["event"] == "ticket.created"]), 20)
            report("webhook ticket.created delivered to their system, signature verified", bool(hooks) and hooks[0]["verified"],
                   f"events={[h['event'] for h in site_log('webhook')]}")

            shipped = await asyncio.to_thread(sdk.send_event, "exchange.shipped", {"orderRef": "5531", "courier": "Delhivery",
                                                                                 "trackingUrl": "https://track.example.in/EX5531"})
            report("exchange.shipped event → customer told, added to the same ticket", shipped.get("notified") and shipped.get("ticket") is not None, str(shipped))

            await c.post(f"{BASE}/v1/api/tickets", headers={"Authorization": f"Bearer {secret}"},
                         json={"customer": {"name": "Rahul Verma", "email": "rahul.verma@example.in"}, "subject": "Still waiting!!",
                               "message": "My exchange for order 5531 is STILL not here. This is really frustrating."})

            async def settled():
                ts = (await c.get(f"{BASE}/v1/api/tickets", params={"email": "rahul.verma@example.in"}, headers={"Authorization": f"Bearer {secret}"})).json()
                return ts if ts and ts[0]["status"] not in ("open",) and len(ts) >= 1 else None
            await asyncio.sleep(8)
            ts = await until(settled, 90) or []
            report("angry follow-up email matched to Rahul → same ticket reopened, not a new one", len(ts) == 1,
                   f"{[(t['ref'], t['status']) for t in ts]}")

            vs = (await c.post(f"{BASE}/v1/voice/sarvam/start", headers={"Authorization": f"Bearer {secret}"},
                               json={"user_phone_number": "+919812345678", "interaction_id": "sv-int-1"})).json()
            tool = (await c.post(f"{BASE}/v1/voice/sarvam/tools/get_order", headers={"Authorization": f"Bearer {secret}"},
                                 json={"order_ref": "5531", "interaction_id": "sv-int-1", "user_identifier": "+919812345678"})).json()
            hook_url = (await c.get(f"{BASE}/v1/voice/sarvam", headers=H)).json()["urls"]["webhook"]
            end = (await c.post(hook_url.replace("https://sahay-536440059442.asia-south1.run.app", BASE).replace("http://localhost:8080", BASE),
                                json={"interaction_id": "sv-int-1", "user_phone_number": "+919812345678", "agent_phone_number": "+918000000000",
                                      "duration": 42.0, "interaction_transcript": [
                                          {"role": "agent", "en_text": vs.get("greeting", "Hello")},
                                          {"role": "user", "en_text": "Where is my exchange for order 5531?"},
                                          {"role": "agent", "en_text": "It has shipped with Delhivery, you'll get it soon."}]})).json()
            report("Sarvam: on-start knows the caller, tool answers in one line, webhook saves the call",
                   "5531" in vs.get("caller_context", "") and bool(tool.get("result")) and bool(end.get("summary")),
                   f"ctx={vs.get('caller_context', '')[:90]!r} tool={tool.get('result', '')[:80]!r} end={end.get('outcome')}")

            d1 = await c.get(f"{BASE}/docs")
            d2 = await c.get(f"{BASE}/docs/commerce-contract.json")
            report("developer docs + OpenAPI contract served", d1.status_code == 200 and d2.status_code == 200 and "Commerce Contract" in d1.text)
        finally:
            site.terminate()
    print(f"\n{sum(results)}/{len(results)} checks passed")


asyncio.run(main())

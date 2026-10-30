"""One-time Brevo setup for Sahay email.

  # check the key, the plan and which sender addresses are verified
  uv run python ../../scripts/brevo_setup.py check

  # register both webhooks: delivery events (bounces show on tickets) and, with --inbound-domain, inbound parsing
  uv run python ../../scripts/brevo_setup.py webhooks --base https://sahay-536440059442.asia-south1.run.app \
      --inbound-domain reply.yourdomain.com

Run from apps/api with BREVO_API_KEY (and EMAIL_WEBHOOK_TOKEN, which Brevo will send back as a bearer token) set.
Inbound parsing needs the domain's MX records pointed at inbound1.sendinblue.com (10) and inbound2.sendinblue.com (20),
and INBOUND_EMAIL_DOMAIN set to the same domain so each business gets <slug>@<domain>.
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))

from app.config import settings  # noqa: E402

API = "https://api.brevo.com/v3"
EVENTS = ["delivered", "hardBounce", "softBounce", "blocked", "invalid", "spam", "deferred", "opened"]


def client() -> httpx.Client:
    if not settings.brevo_api_key:
        sys.exit("Set BREVO_API_KEY first.")
    return httpx.Client(base_url=API, headers={"api-key": settings.brevo_api_key, "accept": "application/json"}, timeout=20)


def check() -> None:
    with client() as c:
        acc = c.get("/account")
        if acc.status_code >= 300:
            sys.exit(f"Brevo refused the key: {acc.status_code} {acc.text[:200]}")
        a = acc.json()
        print(f"account  {a.get('companyName') or a.get('email')}")
        for p in a.get("plan") or []:
            print(f"plan     {p.get('type')}  credits={p.get('credits')} ({p.get('creditsType')})")
        senders = c.get("/senders").json().get("senders") or []
        for s in senders:
            print(f"sender   {'✓' if s.get('active') else '✗'} {s.get('name')} <{s.get('email')}>")
        if not any(s.get("active") for s in senders):
            print("No verified sender yet: add one in Brevo → Senders, Domains & Dedicated IPs.")
        print(f"EMAIL_FROM is {settings.email_from or '(not set)'}")


def webhooks(base: str, inbound_domain: str | None) -> None:
    auth = {"auth": {"type": "bearer", "token": settings.email_webhook_token}} if settings.email_webhook_token else {}
    if not auth:
        print("Warning: EMAIL_WEBHOOK_TOKEN is empty, so anyone could post to the webhooks.")
    base = base.rstrip("/")
    with client() as c:
        have = {(w.get("type"), w.get("url")) for kind in ("transactional", "inbound")
                for w in c.get("/webhooks", params={"type": kind}).json().get("webhooks") or []}
        wanted = [{"type": "transactional", "url": f"{base}/webhooks/email/events", "events": EVENTS, "description": "Sahay delivery status"}]
        if inbound_domain:
            wanted.append({"type": "inbound", "url": f"{base}/webhooks/email", "events": ["inboundEmailProcessed"],
                           "domain": inbound_domain, "description": "Sahay inbound email"})
        for w in wanted:
            if (w["type"], w["url"]) in have:
                print(f"exists   {w['type']} → {w['url']}")
                continue
            r = c.post("/webhooks", json={**w, **auth})
            print(f"{'created' if r.status_code < 300 else 'FAILED '}  {w['type']} → {w['url']}  {r.text[:200]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    w = sub.add_parser("webhooks")
    w.add_argument("--base", default=settings.public_base_url)
    w.add_argument("--inbound-domain", default=settings.inbound_email_domain or None)
    a = p.parse_args()
    check() if a.cmd == "check" else webhooks(a.base, a.inbound_domain)


if __name__ == "__main__":
    main()

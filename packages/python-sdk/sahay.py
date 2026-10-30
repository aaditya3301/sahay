"""sahay: server SDK for businesses with their own website (Python 3.10+, standard library only).

    from sahay import Sahay
    sahay = Sahay(os.environ["SAHAY_SECRET_KEY"])
    sahay.identify(email="riya@example.com")             # {"user": ..., "userHash": ...} for the widget (level 2)
    sahay.send_event("order.shipped", {...})              # keep the AI up to date (level 2)
    status, body = sahay.handle(raw_body, headers, {      # answer Commerce Contract calls (level 3)
        "getOrder": get_order, "checkStock": check_stock, "createReturn": create_return,
    })
"""

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import uuid

DEFAULT_HOST = "https://sahay-536440059442.asia-south1.run.app"
TOLERANCE_S = 300


def _sig(secret: str, ts: str, raw: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()


def _verify(secret: str, raw: str, headers: dict) -> bool:
    h = {k.lower(): v for k, v in headers.items()}
    ts, sig = h.get("x-sahay-timestamp"), h.get("x-sahay-signature")
    if not ts or not sig or abs(time.time() - float(ts)) > TOLERANCE_S:
        return False
    return hmac.compare_digest(_sig(secret, ts, raw), sig)


class Sahay:
    def __init__(self, secret_key: str, host: str = DEFAULT_HOST):
        if not secret_key.startswith("sk_"):
            raise ValueError("Sahay: pass your secret key (sk_...)")
        self.secret_key, self.host = secret_key, host.rstrip("/")

    # -- level 2 ---------------------------------------------------------------
    def identify(self, email: str | None = None, phone: str | None = None, **user) -> dict:
        value = (email or phone or "").strip().lower()
        if not value:
            raise ValueError("identify needs an email or phone")
        return {"user": {"email": email, "phone": phone, **user},
                "userHash": hmac.new(self.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()}

    def send_event(self, type: str, data: dict, id: str | None = None) -> dict:
        eid = id or f"{type}:{data.get('orderRef') or data.get('sku') or uuid.uuid4()}:{data.get('status', '')}"
        return self.request("POST", "/v1/api/events", {"id": eid, "type": type, "data": data})

    def create_ticket(self, customer: dict, message: str, subject: str | None = None, order_ref: str | None = None) -> dict:
        return self.request("POST", "/v1/api/tickets", {"customer": customer, "message": message, "subject": subject, "order_ref": order_ref})

    def list_tickets(self, **query) -> list:
        return self.request("GET", "/v1/api/tickets?" + urllib.parse.urlencode(query))

    def create_lead(self, **lead) -> dict:
        return self.request("POST", "/v1/api/leads", lead)

    def request(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(self.host + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")

    # -- level 3 ---------------------------------------------------------------
    def verify(self, raw_body: str, headers: dict) -> bool:
        return _verify(self.secret_key, raw_body, headers)

    @staticmethod
    def verify_webhook(raw_body: str, headers: dict, webhook_secret: str) -> bool:
        return _verify(webhook_secret, raw_body, headers)

    def handle(self, raw_body: str | bytes, headers: dict, actions: dict) -> tuple[int, dict]:
        """Framework-agnostic: returns (http_status, json_body). Implement only the actions you support."""
        raw = raw_body.decode() if isinstance(raw_body, bytes) else raw_body
        if not self.verify(raw, headers):
            return 401, {"ok": False, "error": "bad_signature"}
        try:
            msg = json.loads(raw)
        except ValueError:
            return 400, {"ok": False, "error": "bad_json"}
        if msg.get("action") == "ping":
            return 200, {"ok": True, "data": {"actions": list(actions)}}
        fn = actions.get(msg.get("action"))
        if not fn:
            return 404, {"ok": False, "error": "not_supported"}
        try:
            return 200, {"ok": True, "data": fn(**(msg.get("params") or {}))}
        except Exception as e:  # noqa: BLE001
            return 500, {"ok": False, "error": str(e)}

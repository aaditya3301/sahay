"""A tiny "custom website" for tests: answers the Sahay Commerce Contract with the Python SDK, and receives our webhooks.

    SAHAY_SECRET_KEY=sk_... python scripts/mock_site.py 8090 8091 scripts/mock_site_log.json
Port 8090: POST /sahay (Commerce Contract). Port 8091: POST /hooks (webhook receiver).
Every call is appended to the log file so tests can check what the AI did on "the website".
"""

import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "python-sdk"))
from sahay import Sahay  # noqa: E402

SITE_PORT, HOOK_PORT, LOG = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
sahay = Sahay(os.environ["SAHAY_SECRET_KEY"], host=os.environ.get("SAHAY_HOST", "http://localhost:8080"))
NOW = datetime.now(timezone.utc)
ORDERS = {
    "5531": {"orderRef": "5531", "customerName": "Rahul Verma", "customerPhone": "+919812345678", "customerEmail": "rahul.verma@example.in",
             "items": [{"sku": "RUN-9-08", "title": "Runner 9 (UK 8)", "qty": 1, "price": 2499}], "total": 2499, "paymentMethod": "prepaid",
             "status": "delivered", "courier": "Delhivery", "trackingNumber": "DLV5531", "deliveredAt": (NOW - timedelta(days=3)).isoformat(),
             "placedAt": (NOW - timedelta(days=7)).isoformat()},
}
STOCK = {"RUN-9-08": 4, "RUN-9-09": 6, "RUN-9-10": 0}
lock = threading.Lock()


def log(entry):
    with lock:
        data = json.loads(LOG.read_text()) if LOG.exists() else []
        data.append({"at": datetime.now().isoformat(), **entry})
        LOG.write_text(json.dumps(data, indent=1))


def find_orders(phone=None, email=None, limit=5):
    return [o for o in ORDERS.values() if (phone and o["customerPhone"] == phone) or (email and o["customerEmail"] == email)][:limit]


def create_return(orderRef, skus=None, reason="", type="return"):
    return {"returnId": f"RET-{orderRef}-1", "type": type, "pickupDate": (NOW + timedelta(days=1)).date().isoformat()}


ACTIONS = {
    "getOrder": lambda orderRef: ORDERS.get(orderRef),
    "findOrders": find_orders,
    "searchProducts": lambda query, limit=5: [{"sku": s, "title": f"Runner 9 (UK {s[-2:].lstrip('0')})", "price": 2499, "stock": n} for s, n in STOCK.items()][:limit],
    "checkStock": lambda sku: {"stock": STOCK.get(sku, 0)},
    "createReturn": create_return,
    "cancelOrder": lambda orderRef, reason="": {"cancelled": True},
}


class Site(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
        if self.server.server_port == HOOK_PORT:
            sf = Path(os.environ.get("WEBHOOK_SECRET_FILE", "-"))
            ok = Sahay.verify_webhook(raw, dict(self.headers), sf.read_text().strip() if sf.exists() else "")
            log({"kind": "webhook", "verified": ok, "event": self.headers.get("X-Sahay-Event"), "body": json.loads(raw)})
            status, body = 200, {"ok": True}
        else:
            status, body = sahay.handle(raw, dict(self.headers), ACTIONS)
            msg = json.loads(raw) if raw else {}
            log({"kind": "contract", "action": msg.get("action"), "params": msg.get("params"), "status": status})
        out = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


for port in (SITE_PORT, HOOK_PORT):
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), Site).serve_forever, daemon=True).start()
print(f"mock site on :{SITE_PORT}, webhooks on :{HOOK_PORT}", flush=True)
threading.Event().wait()

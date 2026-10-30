"""Telegram channel: one platform bot (TELEGRAM_BOT_TOKEN) serves every business on Sahay.

Telegram doesn't tell us who the person is, so a new chat must first prove which order it's about. The bot asks
two questions: the name on the order, then the order ID (or both at once: "LL1042 Priya Sharma"; a phone number
also works instead of the name).
The order id also tells us which business the chat belongs to. Until both match, nothing reaches the AI.
After that, every message goes through the normal pipeline (triage → skills/tools → policy → reply) and the
reply is sent back with the Bot API. /new switches to another order.

Webhook: POST /webhooks/telegram, authenticated by Telegram's X-Telegram-Bot-Api-Secret-Token header.
Local dev without a public URL: TELEGRAM_POLLING=1 reads updates with getUpdates instead.
"""

import asyncio
import difflib
import hashlib
import logging
import re
import unicodedata

import httpx

from app.config import settings
from app.core import norm_phone
from app.db import system_session, tenant_session

log = logging.getLogger("sahay.telegram")
MAX_TRIES = 5
ORDER_RX = re.compile(r"^#?([A-Za-z]{0,6}-?\d{3,12})[.,]?$")


def enabled() -> bool:
    return bool(settings.telegram_bot_token)


def webhook_secret() -> str:
    """Telegram echoes this in X-Telegram-Bot-Api-Secret-Token on every webhook call."""
    return hashlib.sha256(f"{settings.jwt_secret}|telegram".encode()).hexdigest()[:48]


async def api(method: str, payload: dict | None = None, timeout: float = 20):
    # The token is in the URL: never log it (httpx request logging is turned down in main.py).
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}", json=payload or {})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data.get('description', r.status_code)}")
    return data["result"]


async def send_text(chat_id: str, text: str, buttons: list[dict] | None = None) -> str | None:
    payload: dict = {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True}
    if buttons:  # callback data carries the button title, so a tap reads like the customer typed it
        payload["reply_markup"] = {"inline_keyboard": [[{"text": b["title"][:40], "callback_data": b["title"][:64]} for b in buttons[:3]]]}
    res = await api("sendMessage", payload)
    return str(res.get("message_id"))


async def typing(chat_id: str) -> None:
    try:
        await api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=5)
    except Exception:
        pass


async def download(file_id: str) -> bytes:
    f = await api("getFile", {"file_id": file_id})
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{f['file_path']}")
        r.raise_for_status()
        return r.content


# ---------------------------------------------------------------------------
# Who is this? order id + name (or phone)
# ---------------------------------------------------------------------------
def _tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s.lower())
    return [t for t in re.sub(r"[^a-zऀ-ॿ]+", " ", s).split() if t]


def name_matches(typed: str, actual: str) -> bool:
    """The first name must match; every other word typed must appear in the order's name (small typos allowed)."""
    t, a = _tokens(typed), _tokens(actual)
    if not t or not a:
        return False
    close = lambda x, y: x == y or (len(x) >= 4 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.84)  # noqa: E731
    return close(t[0], a[0]) and all(any(close(x, y) for y in a) for x in t)


def parse_claim(text: str) -> tuple[str | None, str | None, str | None]:
    """→ (order_ref, name, phone) from a free-form message like 'LL1042 Priya Sharma' or '#5531 9876543210'."""
    words = text.replace(",", " ").split()
    orders = [w for w in words if ORDER_RX.match(w)]
    phones = [w for w in orders if len(re.sub(r"\D", "", w)) >= 10 and not re.search(r"[A-Za-z]", w)]
    lettered = [w for w in orders if re.search(r"[A-Za-z]", w)]
    order = (lettered or [w for w in orders if w not in phones] or [None])[0]
    rest = [w for w in words if w != order]
    rest_digits = re.sub(r"\D", "", " ".join(rest))
    phone = norm_phone(rest_digits) if len(rest_digits) >= 10 and not _tokens(" ".join(rest)) else None
    name = " ".join(w for w in rest if not re.search(r"\d", w)) or None
    ref = ORDER_RX.match(order).group(1).upper().replace("-", "") if order else None
    return ref, name, phone


async def _chat(chat_id: str) -> dict:
    async with system_session() as db:
        row = await db.one("SELECT * FROM telegram_chats WHERE chat_id=:c", c=chat_id)
        if not row:
            row = await db.one("INSERT INTO telegram_chats (chat_id) VALUES (:c) ON CONFLICT (chat_id) DO UPDATE SET chat_id=excluded.chat_id RETURNING *", c=chat_id)
    return row


async def _save(chat_id: str, **fields) -> None:
    async with system_session() as db:
        await db.exec("UPDATE telegram_chats SET " + ", ".join(f"{k}=:{k}" for k in fields) + ", updated_at=now() WHERE chat_id=:chat_id",
                      chat_id=chat_id, **fields)


async def _find_order(ref: str, prefer: str | None) -> tuple[str, dict, str] | None:
    from app.commerce import get_commerce

    async with system_session() as db:
        tenants = [dict(id=prefer)] if prefer else await db.all("SELECT id FROM tenants WHERE status='active' ORDER BY created_at")
    for t in tenants:
        async with tenant_session(t["id"]) as db:
            try:
                o = await (await get_commerce(db)).get_order(ref)
            except Exception:
                o = None
            if o:
                return str(t["id"]), o, await db.val("SELECT name FROM tenants WHERE id=:t", t=t["id"])
    return None


async def _verify(chat: dict, ref: str, name: str | None, phone: str | None, tg_name: str | None) -> str:
    from app.pipeline import InboundMessage, resolve_customer

    found = await _find_order(ref, chat.get("preferred_tenant") and str(chat["preferred_tenant"]))
    ok = bool(found) and ((name and name_matches(name, found[1].get("customer_name") or ""))
                          or (phone and norm_phone(found[1].get("customer_phone")) == phone))
    if not ok:
        tries = (chat.get("tries") or 0) + 1
        await _save(chat["chat_id"], tries=tries, pending_order=None, pending_name=None)
        left = MAX_TRIES - tries
        return (f"Sorry, I couldn't find order {ref} under that {'phone number' if phone and not name else 'name'}. "
                + (f"({left} {'try' if left == 1 else 'tries'} left) " if left <= 2 else "") + "Let's try again.\n\n" + ASK_NAME)
    tid, order, business = found
    async with tenant_session(tid) as db:
        # a chat belongs to one customer at a time: drop any earlier link of this chat in this business
        await db.exec("DELETE FROM customer_identities WHERE kind='telegram' AND value=:v", v=chat["chat_id"])
        cust = await resolve_customer(db, InboundMessage(channel="telegram", from_phone=order.get("customer_phone"), from_email=order.get("customer_email"),
                                                         from_name=order.get("customer_name"), identity=("telegram", chat["chat_id"]), verified=True))
        from app.core import audit

        await audit(db, "customer", order.get("customer_name"), "telegram.verified", "customer", cust["id"], {"order": order["order_ref"]})
    await _save(chat["chat_id"], tenant_id=tid, customer_id=cust["id"], order_ref=order["order_ref"], verified=True, tries=0,
                pending_order=None, pending_name=None, tg_name=tg_name)
    first = (order.get("customer_name") or "").split(" ")[0] or "there"
    items = ", ".join(i.get("title", "") for i in (order.get("line_items") or [])[:2])
    return (f"Thanks {first}! ✅ I found your {business} order #{order['order_ref']}: {items} ({order.get('status', '').replace('_', ' ')}).\n"
            "Tell me what you need, in Hindi, Hinglish or English, and I'll take care of it.\n"
            "Send /new any time to switch to another order.")


ASK_NAME = "First, what's your full name, as you entered it on your order?"
GREETING = "Namaste! 👋 I'm Sahay, the support assistant for this shop.\nI'll find your order in two quick steps.\n\n" + ASK_NAME
GREETINGS = {"hi", "hii", "hiii", "hello", "helo", "hey", "heyy", "namaste", "namaskar", "hola", "start", "help", "ok", "okay", "yes",
             "no", "hlo", "sup", "yo", "thanks", "thank you", "good morning", "good evening", "good afternoon", "pranam", "ji", "haan"}


def _ask_order(name: str) -> str:
    first = name.split()[0].capitalize()
    return f"Thanks, {first}! Now, what's your order ID? You'll find it in your order confirmation (for example LL1042)."


def _looks_like_name(text: str) -> bool:
    words = text.strip().split()
    return (1 <= len(words) <= 4 and text.strip().lower() not in GREETINGS and "?" not in text
            and all(w[:1].isalpha() for w in words) and not re.search(r"\d", text))


async def _gate(chat: dict, text: str, tg_name: str | None) -> str | None:
    """Two questions before anything reaches the AI: the name on the order, then the order ID.
    Returns the bot's reply while the chat isn't verified yet; None when the message may go to the AI."""
    cmd = text.strip().split()[0].lower() if text.strip() else ""
    if cmd in ("/new", "/reset", "/logout"):
        if chat.get("tenant_id"):
            async with tenant_session(str(chat["tenant_id"])) as db:
                await db.exec("DELETE FROM customer_identities WHERE kind='telegram' AND value=:v", v=chat["chat_id"])
        await _save(chat["chat_id"], verified=False, tenant_id=None, customer_id=None, order_ref=None, pending_order=None, pending_name=None)
        return "Okay, let's start fresh. " + ASK_NAME
    if cmd == "/start":
        payload = text.strip().split()[1:2]
        if payload:  # deep link t.me/<bot>?start=<business-slug> pins the business
            async with system_session() as db:
                t = await db.one("SELECT id FROM tenants WHERE slug=:s", s=payload[0])
            if t:
                await _save(chat["chat_id"], preferred_tenant=t["id"])
        if chat.get("verified"):
            return f"You're connected to order #{chat['order_ref']}. Tell me what you need, or send /new to switch orders."
        await _save(chat["chat_id"], pending_order=None, pending_name=None)
        return GREETING
    if chat.get("verified"):
        return None
    if (chat.get("tries") or 0) >= MAX_TRIES:
        async with system_session() as db:
            locked = await db.val("SELECT updated_at > now() - interval '1 hour' FROM telegram_chats WHERE chat_id=:c", c=chat["chat_id"])
        if locked:
            return "Too many tries. Please try again in an hour, or contact the shop directly."
        await _save(chat["chat_id"], tries=0)
        chat = {**chat, "tries": 0}

    ref, name, phone = parse_claim(text)
    if len(text.split()) == 2 and (joined := parse_claim(text.replace(" ", ""))[0]) and re.search(r"[A-Z]", joined):
        ref, name = joined, None  # "ll 1042" → LL1042
    if ref and (phone or (name and _looks_like_name(name))):  # both in one message: "LL1042 Priya Sharma"
        return await _verify(chat, ref, name, phone, tg_name)
    if not chat.get("pending_name"):  # step 1: the name
        if ref:  # they jumped ahead with the order id: keep it, still ask for the name
            await _save(chat["chat_id"], pending_order=ref)
            return f"Got order {ref}. " + ASK_NAME
        if phone and chat.get("pending_order"):
            return await _verify(chat, chat["pending_order"], None, phone, tg_name)
        if not _looks_like_name(text):
            return ("Namaste! 👋 " if text.strip().lower() in GREETINGS else "") + ASK_NAME
        if chat.get("pending_order"):  # order id came first, this is the name
            return await _verify(chat, chat["pending_order"], text.strip(), None, tg_name)
        await _save(chat["chat_id"], pending_name=text.strip())
        return _ask_order(text.strip())
    # step 2: the order id
    if ref:
        return await _verify(chat, ref, chat["pending_name"], None, tg_name)
    return "Please send just your order ID (for example LL1042). It's in your order confirmation message or email."


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------
async def handle_update(update: dict) -> None:
    from app.pipeline import InboundAttachment, InboundMessage, ingest

    if cq := update.get("callback_query"):  # tapped an inline button: treat it as typed text
        try:
            await api("answerCallbackQuery", {"callback_query_id": cq["id"]}, timeout=5)
        except Exception:
            pass
        msg = {**(cq.get("message") or {}), "text": cq.get("data") or "", "from": cq.get("from"), "message_id": f"cb{cq['id']}"}
    else:
        msg = update.get("message")
    if not msg or not msg.get("chat") or msg["chat"].get("type") != "private":
        return
    chat_id = str(msg["chat"]["id"])
    who = msg.get("from") or {}
    tg_name = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x) or who.get("username")
    text = msg.get("text") or msg.get("caption") or ""
    chat = await _chat(chat_id)
    reply = await _gate(chat, text, tg_name)
    if reply is not None:
        await send_text(chat_id, reply)
        return
    atts = []
    try:
        if msg.get("photo"):
            atts.append(InboundAttachment(await download(msg["photo"][-1]["file_id"]), "image/jpeg", "photo.jpg"))
        elif msg.get("voice"):
            atts.append(InboundAttachment(await download(msg["voice"]["file_id"]), msg["voice"].get("mime_type") or "audio/ogg", "voice.ogg"))
        elif msg.get("document") and (msg["document"].get("mime_type") or "").startswith("image/"):
            atts.append(InboundAttachment(await download(msg["document"]["file_id"]), msg["document"]["mime_type"], msg["document"].get("file_name") or ""))
    except Exception:
        log.warning("telegram media download failed", exc_info=True)
    if not text and not atts:
        await send_text(chat_id, "I can read text, photos and voice notes. What do you need help with?")
        return
    await typing(chat_id)
    await ingest(str(chat["tenant_id"]), InboundMessage(
        channel="telegram", text=text, identity=("telegram", chat_id), thread_id=chat_id, external_id=f"tg:{chat_id}:{msg.get('message_id')}",
        from_name=tg_name, attachments=atts, verified=True))


async def poll_forever() -> None:
    """Dev only (TELEGRAM_POLLING=1): read updates without a public webhook URL."""
    offset = 0
    try:
        await api("deleteWebhook", {"drop_pending_updates": False})
    except Exception:
        pass
    log.info("telegram: polling for updates")
    while True:
        try:
            for u in await api("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]}, timeout=35):
                offset = u["update_id"] + 1
                try:
                    await handle_update(u)
                except Exception:
                    log.exception("telegram update failed")
        except Exception as e:
            log.warning("telegram polling: %s", str(e)[:120])
            await asyncio.sleep(5)


async def connect_webhook() -> dict:
    url = f"{settings.public_base_url.rstrip('/')}/webhooks/telegram"
    await api("setWebhook", {"url": url, "secret_token": webhook_secret(), "allowed_updates": ["message", "callback_query"], "max_connections": 20})
    return await status()


async def status() -> dict:
    if not enabled():
        return {"enabled": False}
    me = await api("getMe")
    info = await api("getWebhookInfo")
    return {"enabled": True, "username": me.get("username"), "link": f"https://t.me/{me.get('username')}",
            "webhook_url": info.get("url") or None, "webhook_expected": f"{settings.public_base_url.rstrip('/')}/webhooks/telegram",
            "pending_updates": info.get("pending_update_count", 0), "last_error": info.get("last_error_message")}

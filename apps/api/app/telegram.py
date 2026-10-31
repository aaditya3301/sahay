"""Telegram channel: one platform bot (TELEGRAM_BOT_TOKEN) serves every business on Sahay.

Telegram doesn't tell us who the person is, so a new chat first asks two questions: their name, then their mobile
number (typed, or shared with Telegram's "Share my number" button, which proves it's their own). The number finds
the customer and the business they buy from (routing.py); a typed number must come with the name on that account.
A t.me/<bot>?start=<business-slug> link pins the business, so new customers of that shop can chat too.
Until then nothing reaches the AI. After that, every message goes through the normal pipeline (triage →
skills/tools → policy → reply) and the reply is sent back with the Bot API. /new starts over.

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
# reply keyboards: ask for the number with Telegram's contact button, then take the keyboard away again
ASK_CONTACT = {"keyboard": [[{"text": "📱 Share my number", "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}
NO_KEYBOARD = {"remove_keyboard": True}


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


async def send_text(chat_id: str, text: str, buttons: list[dict] | None = None, keyboard: dict | None = None) -> str | None:
    payload: dict = {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True}
    if buttons:  # callback data carries the button title, so a tap reads like the customer typed it
        payload["reply_markup"] = {"inline_keyboard": [[{"text": b["title"][:40], "callback_data": b["title"][:64]} for b in buttons[:3]]]}
    elif keyboard:
        payload["reply_markup"] = keyboard
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
# Who is this? name + mobile number
# ---------------------------------------------------------------------------
def _tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s.lower())
    return [t for t in re.sub(r"[^a-zऀ-ॿ]+", " ", s).split() if t]


def name_matches(typed: str, actual: str) -> bool:
    """The first name must match; every other word typed must appear in the account's name (small typos allowed)."""
    t, a = _tokens(typed), _tokens(actual)
    if not t or not a:
        return False
    close = lambda x, y: x == y or (len(x) >= 4 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.84)  # noqa: E731
    return close(t[0], a[0]) and all(any(close(x, y) for y in a) for x in t)


def parse_contact(text: str) -> tuple[str | None, str | None]:
    """→ (name, phone) from a free-form message like 'Priya Sharma', '9876543210' or 'Priya Sharma 98765 43210'."""
    digits = re.sub(r"\D", "", text)
    phone = norm_phone(digits) if 10 <= len(digits) <= 13 else None
    name = " ".join(w for w in text.replace(",", " ").split() if not re.search(r"\d", w) and w not in ("+",)) or None
    return name, phone


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


def _shown(phone: str) -> str:
    return f"{phone[:-10]} {phone[-10:-5]} {phone[-5:]}" if len(phone) > 10 else phone


async def _miss(chat: dict, text: str, keep_phone: str | None = None) -> tuple[str, dict]:
    tries = (chat.get("tries") or 0) + 1
    await _save(chat["chat_id"], tries=tries, pending_name=None, phone=keep_phone)
    left = MAX_TRIES - tries
    return text + (f" ({left} {'try' if left == 1 else 'tries'} left)" if left <= 2 else ""), ASK_CONTACT


async def _connect(chat: dict, name: str | None, phone: str, own_number: bool, tg_name: str | None) -> tuple[str, dict]:
    """Find the customer by their number, link this chat to them, and say which shop they're talking to."""
    from app import routing
    from app.core import audit
    from app.pipeline import InboundMessage, resolve_customer

    prefer = str(chat["preferred_tenant"]) if chat.get("preferred_tenant") else None
    found = await routing.find_customer(phone=phone, only=prefer)
    if found:
        f = found[0]
        if not own_number and not name_matches(name or "", f["name"] or ""):
            # a typed number must come with the name on that account; the contact button proves the number is theirs
            return await _miss(chat, f"That name doesn't match the account for {_shown(phone)}. Please send your full name again, "
                                     "or tap “Share my number” below.", keep_phone=phone)
        tid = f["tenant_id"]
    elif prefer:  # opened from a shop's own link: a new customer of that shop
        tid = prefer
    else:
        return await _miss(chat, f"I couldn't find a shop account with {_shown(phone)}. Please send the number you gave when you ordered, "
                                 "or open this chat from your shop's website.")
    async with tenant_session(tid) as db:
        # a chat belongs to one customer at a time: drop any earlier link of this chat in this business
        await db.exec("DELETE FROM customer_identities WHERE kind='telegram' AND value=:v", v=chat["chat_id"])
        cust = await resolve_customer(db, InboundMessage(channel="telegram", from_phone=phone, from_name=name or tg_name,
                                                         identity=("telegram", chat["chat_id"]), verified=True))
        await audit(db, "customer", cust.get("display_name") or name, "telegram.verified", "customer", cust["id"],
                    {"via": "contact" if own_number else "name_and_phone"})
        business = await db.val("SELECT name FROM tenants WHERE id=:t", t=tid)
        last = await db.one("SELECT order_ref, status FROM commerce_orders WHERE customer_phone=:p AND status <> 'payment_pending' "
                            "ORDER BY placed_at DESC NULLS LAST LIMIT 1", p=phone)
    await _save(chat["chat_id"], tenant_id=tid, customer_id=cust["id"], order_ref=last and last["order_ref"], verified=True, tries=0,
                pending_name=None, pending_order=None, phone=phone, tg_name=tg_name)
    first = (cust.get("display_name") or name or "").split(" ")[0] or "there"
    latest = f" I can see your latest order #{last['order_ref']} ({last['status'].replace('_', ' ')})." if last else ""
    return (f"Thanks {first}! ✅ You're connected to {business}.{latest}\n"
            "Tell me what you need, in Hindi, Hinglish or English, and I'll take care of it.\nSend /new any time to start over."), NO_KEYBOARD


ASK_NAME = "First, what's your full name?"
GREETING = "Namaste! 👋 I'm Sahay, the support assistant for your shop.\nTwo quick questions so I can find your account.\n\n" + ASK_NAME
GREETINGS = {"hi", "hii", "hiii", "hello", "helo", "hey", "heyy", "namaste", "namaskar", "hola", "start", "help", "ok", "okay", "yes",
             "no", "hlo", "sup", "yo", "thanks", "thank you", "good morning", "good evening", "good afternoon", "pranam", "ji", "haan"}


def _ask_phone(name: str) -> str:
    return (f"Thanks, {name.split()[0].capitalize()}! Now your mobile number, the one you gave the shop. "
            "Tap “Share my number” below, or type it.")


def _looks_like_name(text: str) -> bool:
    words = text.strip().split()
    return (1 <= len(words) <= 4 and text.strip().lower() not in GREETINGS and "?" not in text
            and all(w[:1].isalpha() for w in words) and not re.search(r"\d", text))


async def _gate(chat: dict, text: str, tg_name: str | None, contact: dict | None = None,
                sender_id: int | None = None) -> tuple[str, dict | None] | None:
    """Name, then mobile number, before anything reaches the AI.
    Returns (the bot's reply, keyboard) while the chat isn't linked to a customer yet; None when the message may go to the AI."""
    cmd = text.strip().split()[0].lower() if text.strip() else ""
    if cmd in ("/new", "/reset", "/logout"):
        if chat.get("tenant_id"):
            async with tenant_session(str(chat["tenant_id"])) as db:
                await db.exec("DELETE FROM customer_identities WHERE kind='telegram' AND value=:v", v=chat["chat_id"])
        await _save(chat["chat_id"], verified=False, tenant_id=None, customer_id=None, order_ref=None, pending_order=None, pending_name=None, phone=None)
        return "Okay, let's start fresh. " + ASK_NAME, NO_KEYBOARD
    if cmd == "/start":
        payload = text.strip().split()[1:2]
        if payload:  # deep link t.me/<bot>?start=<business-slug> pins the business
            async with system_session() as db:
                t = await db.one("SELECT id FROM tenants WHERE slug=:s", s=payload[0])
            if t:
                await _save(chat["chat_id"], preferred_tenant=t["id"])
        if chat.get("verified"):
            return "You're connected. Tell me what you need, or send /new to start over.", None
        await _save(chat["chat_id"], pending_name=None, phone=None)
        return GREETING, NO_KEYBOARD
    if chat.get("verified"):
        return None
    if (chat.get("tries") or 0) >= MAX_TRIES:
        async with system_session() as db:
            locked = await db.val("SELECT updated_at > now() - interval '1 hour' FROM telegram_chats WHERE chat_id=:c", c=chat["chat_id"])
        if locked:
            return "Too many tries. Please try again in an hour, or contact the shop directly.", NO_KEYBOARD
        await _save(chat["chat_id"], tries=0)
        chat = {**chat, "tries": 0}

    if contact:  # the "Share my number" button: Telegram vouches it's this person's own number
        if sender_id is not None and contact.get("user_id") != sender_id:
            return "Please share your own number with the button below, or type it.", ASK_CONTACT
        name = chat.get("pending_name") or " ".join(x for x in (contact.get("first_name"), contact.get("last_name")) if x) or tg_name
        return await _connect(chat, name, norm_phone(contact.get("phone_number")), True, tg_name)
    name, phone = parse_contact(text)
    if name and not _looks_like_name(name):
        name = None
    if phone and (name or chat.get("pending_name")):  # "Priya Sharma 9876543210", or the number after the name
        return await _connect(chat, name or chat["pending_name"], phone, False, tg_name)
    if phone:  # number first: keep it, still ask for the name
        await _save(chat["chat_id"], phone=phone)
        return f"Got {_shown(phone)}. " + ASK_NAME, NO_KEYBOARD
    if not chat.get("pending_name"):  # step 1: the name
        if not _looks_like_name(text):
            return ("Namaste! 👋 " if text.strip().lower() in GREETINGS else "") + ASK_NAME, None
        if chat.get("phone"):  # the number came first: this is the name
            return await _connect(chat, text.strip(), chat["phone"], False, tg_name)
        await _save(chat["chat_id"], pending_name=text.strip())
        return _ask_phone(text.strip()), ASK_CONTACT
    # step 2: the number
    return "Please share your mobile number: tap “Share my number” below, or type the 10-digit number.", ASK_CONTACT


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
    reply = await _gate(chat, text, tg_name, msg.get("contact"), who.get("id"))
    if reply is not None:
        await send_text(chat_id, reply[0], keyboard=reply[1])
        return
    if msg.get("contact") and not text:  # shared the number again after linking: nothing to answer
        return
    atts = []
    try:
        if msg.get("photo"):
            atts.append(InboundAttachment(await download(msg["photo"][-1]["file_id"]), "image/jpeg", "photo.jpg"))
        elif msg.get("voice"):
            atts.append(InboundAttachment(await download(msg["voice"]["file_id"]), msg["voice"].get("mime_type") or "audio/ogg", "voice.ogg"))
        elif (doc := msg.get("document")) and ((doc.get("mime_type") or "").startswith("image/") or doc.get("mime_type") == "application/pdf"):
            if (doc.get("file_size") or 0) > 10_000_000:
                await send_text(chat_id, "That file is over 10 MB. Please send a smaller photo or PDF.")
                return
            atts.append(InboundAttachment(await download(doc["file_id"]), doc["mime_type"], doc.get("file_name") or ""))
        elif msg.get("document"):
            await send_text(chat_id, "I can read photos and PDF files. Please send the receipt or photo as an image or PDF.")
            return
    except Exception:
        log.warning("telegram media download failed", exc_info=True)
    if not text and not atts:
        await send_text(chat_id, "I can read text, photos and voice notes. What do you need help with?")
        return
    await typing(chat_id)
    await ingest(str(chat["tenant_id"]), InboundMessage(
        channel="telegram", text=text, identity=("telegram", chat_id), thread_id=chat_id, external_id=f"tg:{chat_id}:{msg.get('message_id')}",
        from_name=tg_name, attachments=atts, verified=True, sender=f"@{who['username']}" if who.get("username") else None))


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

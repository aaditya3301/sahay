"""Drive the Telegram channel end to end with a pretend chat: nothing is sent to Telegram (send_text is captured).

    cd apps/api && PYTHONPATH=. uv run python ../../scripts/telegram_flow_check.py

Covers the two questions (name, then order ID), a wrong pairing being refused, the one-message shortcut, order ID
first, a real complaint going through the AI pipeline back to the chat, the ticket on the telegram channel, /new,
and the lock after 5 wrong tries.
"""

import asyncio
import time

from app import telegram
from app.db import system_session, tenant_session

RUN = int(time.time())
sent: list[tuple[str, str]] = []
results = []
chats: list[str] = []


async def fake_send(chat_id, text, buttons=None):
    sent.append((chat_id, text))
    return str(len(sent))


async def fake_typing(chat_id):
    pass


telegram.send_text = fake_send
telegram.typing = fake_typing
_mid = 0


async def say(chat, text):
    global _mid
    _mid += 1
    if chat not in chats:
        chats.append(chat)
    before = len(sent)
    await telegram.handle_update({"update_id": _mid, "message": {"message_id": _mid, "chat": {"id": chat, "type": "private"},
                                                                 "from": {"first_name": "Test"}, "text": text}})
    out = sent[before:]
    return out[-1][1] if out else ""


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail[:140]!r}", flush=True)


async def main():
    a = f"t{RUN}a"
    r = await say(a, "/start")
    check("/start → greets and asks the name first (question 1)", "two quick steps" in r and "full name" in r, r)
    r = await say(a, "hi")
    check("'hi' isn't taken as a name → asks the name again", "full name" in r, r)
    r = await say(a, "Neha Gupta")
    check("name → asks for the order ID (question 2)", "Thanks, Neha" in r and "order ID" in r, r)
    r = await say(a, "LL1042")
    check("order that isn't Neha's → refused, starts over at the name", "couldn't find order LL1042" in r and "full name" in r, r)
    await say(a, "Priya Sharma")
    r = await say(a, "ll 1042")
    check("Priya + 'll 1042' → verified, order shown", "Thanks Priya" in r and "LL1042" in r, r)

    b = f"t{RUN}b"
    r = await say(b, "LL1050 Rahul Verma")
    check("shortcut: both in one message → verified", "Thanks Rahul" in r and "LL1050" in r, r)

    c = f"t{RUN}c"
    r = await say(c, "LL1077")
    check("order ID first → kept, asks the name", "Got order LL1077" in r and "full name" in r, r)
    r = await say(c, "neha")
    check("then the first name → verified", "Thanks Neha" in r and "LL1077" in r, r)

    async with system_session() as db:
        chat = await db.one("SELECT * FROM telegram_chats WHERE chat_id=:c", c=a)
    tid = str(chat["tenant_id"])
    n = len(sent)
    await say(a, "Hi, mera charm set aaya par ek charm toota hua hai 😞 kya karein?")
    t0 = time.time()
    while len(sent) == n and time.time() - t0 < 120:
        await asyncio.sleep(2)
    reply = sent[-1][1] if len(sent) > n else ""
    check("complaint → AI pipeline reply back in the chat", bool(reply) and sent[-1][0] == a, f"{reply} ({int(time.time() - t0)}s)")
    async with tenant_session(tid) as db:
        tk = await db.one("SELECT t.number, t.intent, t.status, c.display_name FROM tickets t JOIN messages m ON m.ticket_id=t.id "
                          "JOIN customers c ON c.id=t.customer_id WHERE m.channel='telegram' AND m.external_id LIKE :x ORDER BY t.created_at DESC LIMIT 1",
                          x=f"tg:{a}:%")
        prefix = await db.val("SELECT ticket_prefix FROM tenant_settings")
    check("ticket in the Inbox on Telegram, linked to Priya", bool(tk) and tk["display_name"] == "Priya Sharma",
          f"{prefix}-{tk['number']} {tk['intent']} {tk['status']}" if tk else "")

    r = await say(a, "/new")
    async with tenant_session(tid) as db:
        linked = await db.val("SELECT count(*) FROM customer_identities WHERE kind='telegram' AND value=:v", v=a)
    check("/new → unlinked, asks the name again", "start fresh" in r and "full name" in r and linked == 0, r)

    d = f"t{RUN}d"
    for _ in range(5):
        await say(d, "LL1050 Wrongname")
    r = await say(d, "LL1050 Rahul Verma")
    check("5 wrong tries → locked even with the right answer", "Too many tries" in r, r)

    async with system_session() as db:
        for ch in chats:
            await db.exec("DELETE FROM telegram_chats WHERE chat_id=:c", c=ch)
    print(f"\n{sum(results)}/{len(results)} checks passed")


asyncio.run(main())

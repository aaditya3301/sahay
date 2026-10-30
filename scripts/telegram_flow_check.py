"""Drive the Telegram channel end to end with a pretend chat: nothing is sent to Telegram (send_text is captured).

    cd apps/api && uv run python ../../scripts/telegram_flow_check.py

Covers: greeting → order id first, then name → wrong name refused → right name verified → a real complaint goes
through the AI pipeline and the reply comes back to the chat → ticket on the telegram channel → /new unlinks →
brute force locks after 5 wrong tries.
"""

import asyncio
import time

from app import telegram
from app.db import system_session, tenant_session

CHAT = f"test-{int(time.time())}"
sent: list[tuple[str, str]] = []
results = []


async def fake_send(chat_id, text, buttons=None):
    sent.append((chat_id, text))
    return str(len(sent))


async def fake_typing(chat_id):
    pass


telegram.send_text = fake_send
telegram.typing = fake_typing
_mid = 0


async def say(text, chat=CHAT):
    global _mid
    _mid += 1
    before = len(sent)
    await telegram.handle_update({"update_id": _mid, "message": {"message_id": _mid, "chat": {"id": chat, "type": "private"},
                                                                 "from": {"first_name": "Test"}, "text": text}})
    return sent[before:]


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def main():
    r = await say("/start")
    check("start → asks for order id + name", r and "order ID and the name" in r[-1][1], r[-1][1][:80] if r else "")
    r = await say("hi")
    check("small talk before verifying → asks again, never guesses", r and "order ID" in r[-1][1])
    r = await say("LL1042")
    check("order id alone → asks for the name", r and "name on that order" in r[-1][1], r[-1][1][:80] if r else "")
    r = await say("Neha Gupta")
    check("wrong name for that order → refused", r and "couldn't match" in r[-1][1], r[-1][1][:90] if r else "")
    r = await say("LL1042 priya")
    check("order id + name → verified, order shown", r and "Thanks Priya" in r[-1][1] and "LL1042" in r[-1][1], r[-1][1][:120] if r else "")
    async with system_session() as db:
        chat = await db.one("SELECT * FROM telegram_chats WHERE chat_id=:c", c=CHAT)
    tid = str(chat["tenant_id"])

    n = len(sent)
    await say("Hi, mera charm set aaya par ek charm toota hua hai 😞 kya karein?")
    t0 = time.time()
    while len(sent) == n and time.time() - t0 < 120:
        await asyncio.sleep(2)
    reply = sent[-1][1] if len(sent) > n else ""
    check("complaint → AI pipeline reply delivered back to the chat", bool(reply) and sent[-1][0] == CHAT, f"{reply[:160]!r} ({int(time.time() - t0)}s)")
    async with tenant_session(tid) as db:
        tk = await db.one("SELECT t.number, t.intent, t.status, c.display_name FROM tickets t JOIN messages m ON m.ticket_id=t.id "
                          "JOIN customers c ON c.id=t.customer_id WHERE m.channel='telegram' AND m.external_id LIKE :x ORDER BY t.created_at DESC LIMIT 1",
                          x=f"tg:{CHAT}:%")
        prefix = await db.val("SELECT ticket_prefix FROM tenant_settings")
    check("ticket in the Inbox on the Telegram channel, linked to the right customer", bool(tk) and tk["display_name"] == "Priya Sharma",
          f"{prefix}-{tk['number']} {tk['intent']} {tk['status']} {tk['display_name']}" if tk else "")

    r = await say("/new")
    async with tenant_session(tid) as db:
        linked = await db.val("SELECT count(*) FROM customer_identities WHERE kind='telegram' AND value=:v", v=CHAT)
    check("/new → chat unlinked from the customer", r and "starting fresh" in r[-1][1].lower() and linked == 0)

    other = CHAT + "-bf"
    for _ in range(5):
        await say("LL1050 Wrongname", chat=other)
    r = await say("LL1050 Rahul", chat=other)
    check("5 wrong tries → locked even with the right name", r and "Too many tries" in r[-1][1], r[-1][1][:70] if r else "")

    async with system_session() as db:
        await db.exec("DELETE FROM telegram_chats WHERE chat_id IN (:a, :b)", a=CHAT, b=other)
    print(f"\n{sum(results)}/{len(results)} checks passed")


asyncio.run(main())

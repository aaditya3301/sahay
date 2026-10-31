"""End-to-end check of the authenticity screening on Telegram and email, with generated test files.

    cd apps/api && PYTHONPATH=. uv run python ../../scripts/authenticity_check.py

Nothing is sent to Telegram (send_text / download are replaced). Files:
  genuine.jpg  – a plain UPI receipt: ₹649 paid to Loop Loot, matches Priya's order LL1042
  forged.jpg   – same layout, but paid to someone else, ₹1,150 matches no order, Photoshop in the EXIF
  edited.pdf   – an invoice PDF re-saved by an online PDF editor (Producer + ModDate after CreationDate)
  ai.png       – a receipt drawn by an image model (skipped if the model is unavailable)
The genuine receipt is then sent again by another customer: same file, same transaction id.
"""

import asyncio
import io
import time
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

from app import telegram
from app.db import system_session, tenant_session
from app.pipeline import InboundAttachment, InboundMessage, ingest

RUN = int(time.time())
FILES: dict[str, tuple[bytes, str]] = {}
sent: list[tuple[str, str]] = []
results = []


def font(size):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def receipt(amount: str, payee: str, upi: str, utr: str, when: datetime, amount_font=None, exif_software=None) -> bytes:
    img = Image.new("RGB", (720, 1280), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 720, 60), fill="#f1f3f4")
    d.text((24, 18), when.strftime("%H:%M"), fill="#202124", font=font(26))
    d.ellipse((310, 160, 410, 260), fill="#1e8e3e")
    d.text((337, 180), "✓", fill="white", font=font(56))
    d.text((360, 330), amount, fill="#202124", font=amount_font or font(72), anchor="mm")
    d.text((360, 410), "Paid to", fill="#5f6368", font=font(26), anchor="mm")
    d.text((360, 452), payee, fill="#202124", font=font(34), anchor="mm")
    d.text((360, 494), upi, fill="#5f6368", font=font(24), anchor="mm")
    d.text((360, 560), "Payment successful", fill="#1e8e3e", font=font(28), anchor="mm")
    d.text((360, 610), when.strftime("%d %b %Y, %I:%M %p"), fill="#5f6368", font=font(24), anchor="mm")
    d.line((60, 680, 660, 680), fill="#dadce0", width=2)
    for i, (k, v) in enumerate([("UPI transaction ID", utr), ("To", f"{payee} ({upi})"), ("From", "Priya Sharma (HDFC Bank ••4821)"),
                                ("Google transaction ID", "CICAgODv7KqH" + utr[-6:])]):
        d.text((60, 710 + i * 80), k, fill="#5f6368", font=font(22))
        d.text((60, 740 + i * 80), v, fill="#202124", font=font(26))
    d.text((360, 1220), "Powered by UPI  |  Google Pay", fill="#9aa0a6", font=font(22), anchor="mm")
    out = io.BytesIO()
    exif = Image.Exif()
    if exif_software:
        exif[0x0131] = exif_software
    img.save(out, "JPEG", quality=88, exif=exif.tobytes() if exif_software else b"")
    return out.getvalue()


def edited_pdf() -> bytes:
    body = ("BT /F1 18 Tf 72 720 Td (Loop Loot - Tax Invoice) Tj ET\n"
            "BT /F1 12 Tf 72 690 Td (Invoice LL-INV-1042   Order LL1042   Date 24 Oct 2026) Tj ET\n"
            "BT /F1 12 Tf 72 660 Td (Celestial Charm Set x1 ........ Rs 1,649.00) Tj ET\n"
            "BT /F1 12 Tf 72 630 Td (Total paid (UPI) ............... Rs 1,649.00) Tj ET\n")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(body)} >>\nstream\n{body}endstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Producer (iLovePDF) /Creator (Sejda PDF editor) /CreationDate (D:20261024101500) /ModDate (D:20261030221000) >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


async def ai_receipt() -> bytes | None:
    from google.genai import types

    from app import llm
    from app.config import settings

    try:
        r = await llm.client().aio.models.generate_content(
            model=settings.model_image, contents="A realistic smartphone screenshot of a Google Pay UPI payment success screen: ₹2,499 paid to Loop Loot, with a UPI transaction ID",
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
        for p in r.candidates[0].content.parts:
            if p.inline_data:
                return p.inline_data.data
    except Exception as e:
        print("  (AI image skipped:", str(e)[:100], ")")
    return None


async def fake_send(chat_id, text, buttons=None):
    sent.append((chat_id, text))
    return str(len(sent))


async def fake_typing(chat_id):
    pass


async def fake_download(file_id):
    return FILES[file_id][0]


telegram.send_text, telegram.typing, telegram.download = fake_send, fake_typing, fake_download
_mid = 0


async def tg(chat, text="", photo=None, document=None):
    global _mid
    _mid += 1
    msg = {"message_id": _mid, "chat": {"id": chat, "type": "private"}, "from": {"first_name": "Test"}, "text": text}
    if photo:
        msg = {**msg, "text": None, "caption": text, "photo": [{"file_id": photo}]}
    if document:
        msg = {**msg, "text": None, "caption": text, "document": {"file_id": document, "mime_type": FILES[document][1], "file_name": document, "file_size": len(FILES[document][0])}}
    await telegram.handle_update({"update_id": _mid, "message": msg})


async def screened(tid, sha_like_name, timeout=150):
    t0 = time.time()
    while time.time() - t0 < timeout:
        async with tenant_session(tid) as db:
            row = await db.one("SELECT screening FROM attachments WHERE filename=:f AND screening IS NOT NULL ORDER BY created_at DESC LIMIT 1", f=sha_like_name)
        if row:
            return row["screening"]
        await asyncio.sleep(3)
    return None


def check(name, ok, sc):
    results.append(ok)
    why = ", ".join(s["kind"] for s in (sc or {}).get("signals", []) if s["score"] > 0) if sc else "no result"
    print(f"{'PASS' if ok else 'FAIL'}  {name}  -> {sc and sc['verdict']} ({sc and sc['score']}) kind={sc and sc['kind']} signals=[{why}]", flush=True)


async def main():
    now = datetime.now() - timedelta(days=6)
    utr = "4" + str(RUN)[-11:].rjust(11, "0")
    FILES[f"genuine-{RUN}.jpg"] = (receipt("₹649", "Loop Loot", "looploot@okhdfcbank", utr, now), "image/jpeg")
    FILES[f"forged-{RUN}.jpg"] = (receipt("₹1,150", "Rahul Kumar", "rahulk1987@ybl", "8" + str(RUN)[-11:].rjust(11, "1"), now,
                                          amount_font=font(64), exif_software="Adobe Photoshop 25.0 (Windows)"), "image/jpeg")
    FILES[f"edited-{RUN}.pdf"] = (edited_pdf(), "application/pdf")
    ai = await ai_receipt()
    if ai:
        FILES[f"ai-{RUN}.png"] = (ai, "image/png")

    a, b = f"auth{RUN}a", f"auth{RUN}b"
    for chat, name, ref in ((a, "Priya Sharma", "LL1042"), (b, "Rahul Verma", "LL1050")):
        await tg(chat, name)
        await tg(chat, ref)
    async with system_session() as db:
        tid = str((await db.one("SELECT tenant_id FROM telegram_chats WHERE chat_id=:c", c=a))["tenant_id"])

    await tg(a, "I already paid by UPI, please check", document=f"genuine-{RUN}.jpg")
    sc = await screened(tid, f"genuine-{RUN}.jpg")
    check("Telegram: genuine receipt matching the order is not flagged as fake", bool(sc) and sc["verdict"] != "likely_fake"
          and not any(s["kind"] in ("payee_not_this_business", "amount_matches_no_order") for s in sc["signals"]), sc)

    await tg(a, "Here is my payment proof, refund me", document=f"forged-{RUN}.jpg")
    sc = await screened(tid, f"forged-{RUN}.jpg")
    check("Telegram: forged receipt (other payee, no matching amount, Photoshop) is flagged", bool(sc) and sc["verdict"] in ("suspicious", "likely_fake")
          and {"payee_not_this_business", "edited_with_software"} <= {s["kind"] for s in sc["signals"]}, sc)

    await tg(b, "Paid already, see screenshot", document=f"genuine-{RUN}.jpg")
    t0 = time.time()
    sc = None
    while time.time() - t0 < 150 and not sc:
        async with tenant_session(tid) as db:
            row = await db.one("SELECT a.screening FROM attachments a JOIN messages m ON m.id=a.message_id WHERE a.filename=:f AND m.external_id LIKE :x "
                               "AND a.screening IS NOT NULL", f=f"genuine-{RUN}.jpg", x=f"tg:{b}:%")
        sc = row and row["screening"]
        await asyncio.sleep(3)
    check("Telegram: same receipt re-sent by another customer → reused file + transaction id caught",
          bool(sc) and {"duplicate_file", "transaction_id_used_before"} & {s["kind"] for s in sc["signals"]} and sc["verdict"] != "genuine", sc)

    m = await ingest(tid, InboundMessage(channel="email", text="Subject: Refund for LL1042\n\nPlease see the attached invoice, I was overcharged.",
                                         from_email="priya.sharma@example.in", from_name="Priya Sharma", subject="Refund for LL1042", verified=True,
                                         attachments=[InboundAttachment(FILES[f"edited-{RUN}.pdf"][0], "application/pdf", f"edited-{RUN}.pdf")]))
    sc = await screened(tid, f"edited-{RUN}.pdf")
    check("Email: invoice PDF re-saved in an online PDF editor is flagged", bool(sc) and sc["verdict"] != "genuine"
          and {"pdf_edited_with_tool", "pdf_modified_after_creation"} & {s["kind"] for s in sc["signals"]}, sc)

    if ai:
        await ingest(tid, InboundMessage(channel="email", text="Subject: Payment done\n\nPaid ₹2,499, screenshot attached.",
                                         from_email="priya.sharma@example.in", from_name="Priya Sharma", subject="Payment done", verified=True,
                                         attachments=[InboundAttachment(ai, "image/png", f"ai-{RUN}.png")]))
        sc = await screened(tid, f"ai-{RUN}.png")
        check("Email: AI-generated receipt is not passed as genuine", bool(sc) and sc["verdict"] != "genuine", sc)

    await asyncio.sleep(25)  # let the AI replies to the forged receipt go out
    forged_reply = next((t for c, t in reversed(sent) if c == a and len(t) > 40), "")
    print("AI reply after the forged receipt:", forged_reply[:260].replace("\n", " "))
    lower = forged_reply.lower()
    check("AI doesn't accuse the customer of faking", bool(forged_reply) and "fake" not in lower and "forg" not in lower, {"verdict": "-", "score": "-", "kind": "-", "signals": []})

    async with system_session() as db:
        await db.exec("DELETE FROM telegram_chats WHERE chat_id IN (:a, :b)", a=a, b=b)
    print(f"\n{sum(bool(r) for r in results)}/{len(results)} checks passed")


asyncio.run(main())

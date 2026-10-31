"""Channel adapters: intake parsing + reply dispatch.

Each tenant's channel credentials live in `integrations` (kind = whatsapp | email). When a channel
isn't configured, replies are recorded with status 'simulated' and show up in the in-app
customer/owner simulator, so the full flow works before Meta/Brevo are set up.
"""

import hashlib
import hmac
import logging
import re

import httpx

from app.config import settings
from app.core import inr, norm_phone

log = logging.getLogger("sahay.channels")


def sarvam_defaults() -> dict:
    """Platform-wide Sarvam Voice Agents details from the server's environment (blank ones are left out)."""
    d = {"org_id": settings.sarvam_org_id, "workspace_id": settings.sarvam_workspace_id, "app_id": settings.sarvam_app_id,
         "app_version": settings.sarvam_app_version, "connection_id": settings.sarvam_connection_id,
         "agent_phone_number": settings.sarvam_agent_phone, "deployment_id": settings.sarvam_deployment_id}
    return {k: v for k, v in d.items() if v}


async def integration(db, kind: str, defaults: bool = True) -> dict:
    """A business's saved connection settings. For Sarvam, anything the business left blank falls back to the platform defaults."""
    from app.core import unseal

    if kind == "email":
        # For now all email goes through the platform's central account. The addresses a business types in are kept
        # on file but deliberately not used for sending or receiving.
        return {}
    row = await db.one("SELECT config FROM integrations WHERE kind=:k AND status='active'", k=kind)
    cfg = unseal((row or {}).get("config") or {})
    if kind == "sarvam" and defaults:
        cfg = {**sarvam_defaults(), **{k: v for k, v in cfg.items() if v not in (None, "")}}
    return cfg


# ---------------------------------------------------------------------------
# WhatsApp Cloud API
# ---------------------------------------------------------------------------
def verify_meta_signature(body: bytes, header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        return True  # not configured (dev)
    if not header:
        return False
    expected = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def wa_send(cfg: dict, to: str, payload: dict) -> str | None:
    url = f"https://graph.facebook.com/{settings.whatsapp_graph_version}/{cfg['phone_number_id']}/messages"
    body = {"messaging_product": "whatsapp", "to": to.lstrip("+"), **payload}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, json=body, headers={"Authorization": f"Bearer {cfg['access_token']}"})
        if r.status_code >= 300:
            log.warning("whatsapp send failed: %s %s", r.status_code, r.text[:300])
            r.raise_for_status()
        return r.json().get("messages", [{}])[0].get("id")


async def wa_download_media(cfg: dict, media_id: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=30) as c:
        h = {"Authorization": f"Bearer {cfg['access_token']}"}
        meta = (await c.get(f"https://graph.facebook.com/{settings.whatsapp_graph_version}/{media_id}", headers=h)).json()
        data = (await c.get(meta["url"], headers=h)).content
        return data, meta.get("mime_type", "application/octet-stream")


def wa_interactive_buttons(text: str, buttons: list[tuple[str, str]]) -> dict:
    return {"type": "interactive", "interactive": {
        "type": "button", "body": {"text": text[:1024]},
        "action": {"buttons": [{"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}} for bid, title in buttons[:3]]},
    }}


# ---------------------------------------------------------------------------
# Email: Brevo transactional API (a business's key, or the platform's). Businesses that saved a Resend key earlier
# keep sending through Resend; the key's prefix tells them apart (Brevo keys start with xkeysib-).
# ---------------------------------------------------------------------------
BREVO_SEND = "https://api.brevo.com/v3/smtp/email"


def email_key(cfg: dict) -> tuple[str, str] | None:
    """(provider, api_key) for sending, or None while email is simulated."""
    if key := cfg.get("api_key"):
        return ("resend", key) if key.startswith("re_") else ("brevo", key)
    if settings.brevo_api_key:
        return "brevo", settings.brevo_api_key
    if settings.resend_api_key:
        return "resend", settings.resend_api_key
    if settings.email_imap_user and settings.email_imap_password:  # the central mailbox sends its own replies (SMTP, same app password)
        return "smtp", settings.email_imap_password
    return None


def email_ready(cfg: dict) -> bool:
    return bool(email_key(cfg) and (cfg.get("from") or settings.email_from or settings.email_imap_user))


class EmailError(RuntimeError):
    """A send that failed for a reason the owner can act on (shown on the lead or message)."""


# RFC 2606/6761 names that can never receive mail (the demo data uses them); sending there only burns quota and reputation
RESERVED_TLDS = {"test", "example", "invalid", "localhost", "local"}


def undeliverable(addr: str | None) -> bool:
    domain = (addr or "").rsplit("@", 1)[-1].strip().lower().rstrip(">")
    labels = domain.split(".")
    return "@" not in (addr or "") or len(labels) < 2 or labels[-1] in RESERVED_TLDS or "example" in labels[:-1]


def _brevo_reason(status: int, text: str) -> str:
    try:
        d = httpx.Response(status, text=text).json()
        msg, code = d.get("message", ""), d.get("code", "")
    except Exception:
        msg, code = text[:160], ""
    if "unrecognised IP" in msg or "unrecognized IP" in msg:
        return "Brevo blocked the server's IP address. In Brevo → Security → Authorised IPs, choose Deactivate blocking."
    if "sender" in msg.lower() and ("valid" in msg.lower() or "verif" in msg.lower()):
        return f"Brevo refused the sender address: verify it in Brevo → Senders ({msg[:120]})"
    if code == "not_enough_credits" or "credit" in msg.lower():
        return "Brevo's daily sending limit is used up (free plan: 300 a day)."
    return f"Brevo said: {msg[:160] or status}"


def _mailbox(addr: str) -> dict:
    """'Loop Loot <help@looploot.in>' → {"name": "Loop Loot", "email": "help@looploot.in"}"""
    name, _, email = addr.rpartition("<")
    if not email:
        return {"email": addr.strip()}
    out = {"email": email.rstrip(">").strip()}
    if name.strip().strip('"'):
        out["name"] = name.strip().strip('"')[:70]
    return out


_URL = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\]])")


def _inline(s: str) -> str:
    """Escape one line of text for HTML and turn web addresses into links."""
    import html

    return _URL.sub(lambda m: f'<a href="{m.group(1)}" style="color:#226192">{m.group(1)}</a>', html.escape(s))


def _blocks(text: str) -> list[str]:
    """Paragraphs of a plain-text email: blank lines separate them. A long text that came back as one block is cut
    into short paragraphs at sentence ends, so it still reads like an email."""
    paras = [p for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if p.strip()]
    if len(paras) == 1 and "\n" not in paras[0] and len(paras[0]) > 420:
        paras, cur = [], ""
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if cur and len(cur) + len(sentence) > 260:
                paras.append(cur)
                cur = sentence
            else:
                cur = f"{cur} {sentence}".strip()
        paras.append(cur)
    return paras


def _render(paras: list[str], style: str) -> str:
    """Paragraphs → <p>; lines that start with "- " become a bulleted list, even right under a lead-in line."""
    bullet = re.compile(r"^[-•*]\s+")
    out = []
    for p in paras:
        run: list[str] = []  # the current run of lines, all bullets or all text

        def flush() -> None:
            if not run:
                return
            if bullet.match(run[0]):
                items = "".join(f'<li style="margin:0 0 6px">{_inline(bullet.sub("", ln))}</li>' for ln in run)
                out.append(f'<ul style="{style};padding-left:22px">{items}</ul>')
            else:
                out.append(f'<p style="{style}">' + "<br>".join(_inline(ln) for ln in run) + "</p>")
            run.clear()

        for ln in (x.strip() for x in p.split("\n")):
            if ln and run and bool(bullet.match(ln)) != bool(bullet.match(run[0])):
                flush()
            if ln:
                run.append(ln)
        flush()
    return "".join(out)


def _html(text: str) -> str:
    """Conversation emails: plain, readable paragraphs."""
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#222">'
            + _render(_blocks(text), "margin:0 0 14px") + "</div>")


def campaign_html(brand: str, text: str, cta_url: str | None = None, cta_label: str = "Shop now") -> str:
    """A campaign or newsletter as a clean branded email: the business name on top, the message in paragraphs (and a
    product list), an optional button to the store. There is no unsubscribe link or button."""
    import html

    body = _render(_blocks(text), "margin:0 0 18px;font-size:16px;line-height:1.7;color:#2b3a42")
    button = (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:10px 0 8px"><tr>'
        '<td style="background:#ef8557;border-radius:999px">'
        f'<a href="{html.escape(cta_url, quote=True)}" style="display:inline-block;padding:13px 30px;font-size:15px;font-weight:700;'
        f'color:#17394e;text-decoration:none">{html.escape(cta_label)}</a></td></tr></table>') if cta_url else ""
    name = html.escape(brand)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f1ede4">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1ede4;padding:28px 12px"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden;font-family:Arial,Helvetica,sans-serif">'
        f'<tr><td style="background:#17394e;padding:24px 32px;color:#ffffff;font-size:21px;font-weight:700;letter-spacing:.2px">{name}</td></tr>'
        f'<tr><td style="padding:32px 32px 14px">{body}{button}</td></tr>'
        f'<tr><td style="padding:18px 32px 26px;border-top:1px solid #ece7dc;color:#8a8f93;font-size:12px;line-height:1.5">'
        f"You're getting this because you've ordered from {name}.</td></tr>"
        '</table></td></tr></table></body></html>')


def _smtp_send(sender: str, to: str, subject: str, text: str, reply_to: str, thread: dict, html_body: str | None = None) -> str:
    """Send through the central mailbox's own SMTP server; returns the Message-ID (a customer's reply quotes it)."""
    import smtplib
    from email.message import EmailMessage
    from email.utils import make_msgid

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Reply-To"] = sender, to, subject[:250], reply_to
    msg["Message-ID"] = mid = make_msgid(domain=settings.email_imap_user.rsplit("@", 1)[-1])
    for k, v in thread.items():
        msg[k] = v
    msg.set_content(text)
    msg.add_alternative(html_body or _html(text), subtype="html")
    with smtplib.SMTP_SSL(settings.email_smtp_host, 465, timeout=30) as s:
        s.login(settings.email_imap_user, settings.email_imap_password)
        s.send_message(msg)
    return mid


async def email_send(cfg: dict, to: str, subject: str, text: str, in_reply_to: str | None = None,
                     sender: str | None = None, reply_to: str | None = None, tenant_id: str | None = None,
                     brand: str | None = None, html: str | None = None) -> str | None:
    """Send one email (plain text, plus `html` when given, else a simple HTML copy of the text); returns the provider's
    message id (Brevo's is the SMTP Message-ID, which replies quote).
    brand names a bare sender address, so the shop's name shows instead of the platform's mailbox."""
    provider, key = email_key(cfg) or (None, None)
    sender = sender or cfg.get("from") or settings.email_from or settings.email_imap_user
    if not key or not sender:
        raise EmailError("Email isn't connected (add a Brevo key and a verified sender).")
    if provider == "smtp":  # Gmail only sends as the signed-in mailbox, so that is always the address
        sender = settings.email_imap_user
    if brand and "<" not in sender:
        sender = f"{brand} <{sender}>"
    reply_to = reply_to or cfg.get("reply_to") or sender
    thread = {"In-Reply-To": in_reply_to, "References": in_reply_to} if in_reply_to else {}
    if provider == "smtp":
        import asyncio

        return await asyncio.to_thread(_smtp_send, sender, to, subject, text, reply_to, thread, html)
    async with httpx.AsyncClient(timeout=20) as c:
        if provider == "brevo":
            r = await c.post(BREVO_SEND, headers={"api-key": key, "accept": "application/json"}, json={
                "sender": _mailbox(sender), "to": [{"email": to}], "subject": subject[:250], "textContent": text, "htmlContent": html or _html(text),
                "replyTo": _mailbox(reply_to), **({"headers": thread} if thread else {}), "tags": ["sahay"] + ([f"t:{tenant_id}"] if tenant_id else [])})  # Brevo echoes tags on delivery events
            if r.status_code >= 300:
                log.warning("brevo send failed: %s %s", r.status_code, r.text[:300])
                raise EmailError(_brevo_reason(r.status_code, r.text))
            return r.json().get("messageId")
        r = await c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}"},
                         json={"from": sender, "to": [to], "subject": subject, "text": text, "headers": thread, "reply_to": reply_to})
        r.raise_for_status()
        return r.json().get("id")


async def inbound_address(tenant_id: str) -> str | None:
    """Where this business receives email (customers' replies come back here and become tickets). Only once Brevo inbound
    parsing is wired (INBOUND_EMAIL_DOMAIN): until then replies go to the sender's own mailbox instead of nowhere."""
    from app.db import system_session

    if not settings.inbound_email_domain:
        return None
    async with system_session() as db:
        return await db.val("SELECT external_key FROM channel_routes WHERE channel='email' AND tenant_id=:t ORDER BY external_key LIMIT 1", t=tenant_id)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
async def send_to_customer(db, conversation: dict, text: str, ticket_id=None, author_type: str = "ai",
                           author_name: str | None = None, interactive: dict | None = None, subject: str | None = None,
                           html: str | None = None) -> dict:
    """Store the outbound message and deliver it on the conversation's channel (`html`: a formatted version, for email)."""
    channel = conversation["channel"]
    status, external_id = "sent", None
    customer = await db.one("SELECT * FROM customers WHERE id=:c", c=conversation["customer_id"])
    try:
        if channel == "whatsapp":
            cfg = await integration(db, "whatsapp")
            if cfg.get("access_token") and cfg.get("phone_number_id"):
                to = customer["primary_phone"]
                payload = (wa_interactive_buttons(text, [(b["id"], b["title"]) for b in interactive["buttons"]]) if interactive and interactive.get("buttons")
                           else {"type": "text", "text": {"body": text[:4096]}})
                external_id = await wa_send(cfg, to, payload)
            else:
                status = "simulated"
        elif channel == "email":
            # inbound emails reach the model as "Subject: …\n\nbody", and drafts sometimes copy that first line
            text = re.sub(r"\A\s*Subject:[^\n]*\n+", "", text)
            cfg = await integration(db, "email")
            if undeliverable(customer.get("primary_email")):
                status = "skipped"  # demo or reserved address: never reaches anyone
            elif email_ready(cfg):
                last_in = await db.one(
                    "SELECT external_id FROM messages WHERE conversation_id=:c AND direction='in' ORDER BY created_at DESC LIMIT 1",
                    c=conversation["id"],
                )
                tsubject = await db.val("SELECT subject FROM tickets WHERE id=:t", t=ticket_id) if ticket_id else None
                external_id = await email_send(cfg, customer["primary_email"], subject or f"Re: {tsubject or 'your request'}", text,
                                               None if subject else (last_in or {}).get("external_id"),
                                               reply_to=cfg.get("reply_to") or await inbound_address(db.tenant_id),
                                               tenant_id=db.tenant_id, brand=await db.val("SELECT name FROM tenants WHERE id=:t", t=db.tenant_id),
                                               html=html)
            else:
                status = "simulated"
        elif channel == "telegram":
            from app import telegram

            chat_id = conversation.get("external_thread_id") or await db.val(
                "SELECT value FROM customer_identities WHERE customer_id=:c AND kind='telegram' LIMIT 1", c=conversation["customer_id"])
            if telegram.enabled() and chat_id:
                mid = await telegram.send_text(chat_id, text, (interactive or {}).get("buttons"))
                external_id = f"tg-out:{chat_id}:{mid}"  # Telegram message ids are per chat; inbound ones use "tg:"
            else:
                status = "simulated"
        else:  # webchat / sim: the widget/simulator polls our API
            status = "sent"
    except Exception as e:
        log.exception("dispatch failed")
        status = "failed"
        text = text + f"\n\n[delivery failed: {str(e)[:120]}]"
    row = await db.one(
        "INSERT INTO messages (tenant_id, conversation_id, ticket_id, customer_id, direction, channel, author_type, author_name, "
        "text, external_id, status, interactive) VALUES (:t,:c,:tk,:cu,'out',:ch,:at,:an,:tx,:ex,:st,:ia) RETURNING *",
        t=db.tenant_id, c=conversation["id"], tk=ticket_id, cu=conversation["customer_id"], ch=channel, at=author_type,
        an=author_name, tx=text, ex=external_id, st=status, ia=interactive,
    )
    await db.exec("UPDATE conversations SET last_outbound_at=now() WHERE id=:c", c=conversation["id"])
    if ticket_id:
        await db.exec("UPDATE tickets SET first_response_at=coalesce(first_response_at, now()), updated_at=now() WHERE id=:t", t=ticket_id)
    return row


async def notify_owner_approval(tenant_id: str, approval_id: str) -> None:
    """Send an approval request to the owner's WhatsApp (or the simulated owner phone)."""
    from app.approvals import approval_token
    from app.db import tenant_session

    async with tenant_session(tenant_id) as db:
        s = await db.one("SELECT owner_phone, owner_wa_approvals FROM tenant_settings")
        ap = await db.one(
            "SELECT a.*, t.number, c.display_name FROM approvals a JOIN tickets t ON t.id=a.ticket_id "
            "JOIN customers c ON c.id=t.customer_id WHERE a.id=:id", id=approval_id,
        )
        if not s or not s["owner_wa_approvals"] or not ap:
            return
        text = f"🔔 Approval needed · Ticket #{ap['number']}\n{ap['summary']}\n{ap['suggestion'] or ''}"
        buttons = [(approval_token(approval_id, "approve"), "Approve"), (approval_token(approval_id, "reject"), "Reject")]
        cfg = await integration(db, "whatsapp")
        sent_via = "simulated"
        if cfg.get("access_token") and s["owner_phone"]:
            try:
                await wa_send(cfg, norm_phone(s["owner_phone"]), wa_interactive_buttons(text, buttons))
                sent_via = "whatsapp"
            except Exception:
                log.exception("owner WA notify failed")
        await db.exec(
            "INSERT INTO audit_log (tenant_id, actor_type, verb, object_type, object_id, data) VALUES (:t,'system','owner.notified','approval',:id,:d)",
            t=tenant_id, id=approval_id, d={"via": sent_via, "text": text},
        )


def owner_message_preview(ap: dict) -> dict:
    from app.approvals import approval_token

    return {
        "approval_id": str(ap["id"]),
        "text": f"🔔 Approval needed · Ticket #{ap.get('number')}\n{ap['summary']}\n{ap.get('suggestion') or ''}",
        "buttons": [{"id": approval_token(str(ap["id"]), "approve"), "title": "Approve"},
                    {"id": approval_token(str(ap["id"]), "reject"), "title": "Reject"}],
        "status": ap["status"],
        "created_at": ap["created_at"],
        "value": inr(ap.get("value_paise")) if ap.get("value_paise") else None,
    }

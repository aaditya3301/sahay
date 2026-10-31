"""Central mailbox over IMAP: customers write to one address (for example a Gmail inbox) and each new email becomes a
ticket for the business that knows the sender, through the same pipeline as every other channel (triage → tools →
policy → reply or escalation). Replies go back by email from the same address.

Setup (Gmail): turn on 2-Step Verification, create an app password (Google Account → Security → App passwords) and set
EMAIL_IMAP_USER + EMAIL_IMAP_PASSWORD. The mailbox is opened read-only (nothing in it changes, not even unread marks).
Only mail that arrives after the first start is read; newsletters and automatic mail are skipped.

Which business? 1) a reply to one of our emails goes to the business that sent it, 2) mail to an address a business
receives on, 3) the business the sender is a customer of (routing.py), 4) the one business that lists this mailbox as
its receiving address. Anyone else is skipped: no business knows them.
"""

import asyncio
import email
import html
import imaplib
import logging
import re
from email import policy
from email.utils import getaddresses, parseaddr

from app import routing
from app.config import settings
from app.core import norm_email, unseal
from app.db import system_session, tenant_session

log = logging.getLogger("sahay.email")
BATCH = 25  # messages handled per poll
SKIP_SENDERS = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?|notifications?)([+.@-]|$)", re.I)


def enabled() -> bool:
    return bool(settings.email_imap_user and settings.email_imap_password)


def mailbox() -> str:
    return norm_email(settings.email_imap_user) or ""


# ---------------------------------------------------------------------------
# Reading the mailbox (blocking imaplib, run in a thread)
# ---------------------------------------------------------------------------
def _fetch(last_uid: int | None, validity: int | None) -> tuple[int, int, list[tuple[int, bytes]]]:
    """New messages after last_uid → (uid validity, newest uid handled, [(uid, raw message)])."""
    m = imaplib.IMAP4_SSL(settings.email_imap_host, 993, timeout=30)
    try:
        m.login(settings.email_imap_user, settings.email_imap_password)
        m.select("INBOX", readonly=True)
        _, st = m.status("INBOX", "(UIDNEXT UIDVALIDITY)")
        nums = dict(re.findall(r"(UIDNEXT|UIDVALIDITY) (\d+)", st[0].decode()))
        uv, newest = int(nums["UIDVALIDITY"]), int(nums["UIDNEXT"]) - 1
        if last_uid is None or validity != uv:  # first start (or the mailbox was rebuilt): begin from now, never answer old mail
            return uv, newest, []
        if newest <= last_uid:
            return uv, last_uid, []
        _, data = m.uid("search", None, f"UID {last_uid + 1}:*")  # "n:*" always includes the newest message, so filter
        uids = sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > last_uid)[:BATCH]
        out = []
        for uid in uids:
            _, d = m.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((part[1] for part in d if isinstance(part, tuple)), None)
            if raw:
                out.append((uid, raw))
        return uv, (uids[-1] if uids else newest), out
    finally:
        try:
            m.logout()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _html_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def strip_quoted(text: str) -> str:
    """Only what the customer wrote this time: cut at the quoted earlier mail ("On … wrote:", "> …", Outlook's header block)."""
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        joined = (s + " " + lines[i + 1].strip()) if i + 1 < len(lines) else s  # Gmail wraps "On …, X <y>\nwrote:"
        if (s.startswith(">") or re.match(r"^On .+wrote:$", s) or re.match(r"^On .+wrote:$", joined)
                or re.match(r"^-{2,}\s*Original Message", s, re.I) or re.match(r"^From: .+", s) and out and not out[-1].strip()):
            break
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def automatic(msg) -> bool:
    """Newsletters, bulk mail, auto-replies and bounces: never a customer question."""
    auto = str(msg.get("Auto-Submitted", "no")).strip().lower()
    prec = str(msg.get("Precedence", "")).strip().lower()
    return (auto not in ("", "no") or prec in ("bulk", "list", "junk", "auto_reply") or bool(msg.get("List-Id")) or bool(msg.get("List-Unsubscribe"))
            or bool(msg.get("X-Autoreply")) or bool(msg.get("X-Autorespond")))


def parse(raw: bytes) -> dict:
    from app.pipeline import InboundAttachment

    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    tos = [a for _, a in getaddresses([str(v) for k in ("To", "Cc", "Delivered-To") for v in msg.get_all(k, [])])]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            text = body.get_content()
        except Exception:
            text = (body.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if body.get_content_type() == "text/html":
            text = _html_text(text)
    atts = []
    for part in msg.iter_attachments():
        ctype = part.get_content_type()
        if (ctype.startswith("image/") or ctype == "application/pdf") and len(atts) < 5:
            data = part.get_payload(decode=True) or b""
            if 0 < len(data) <= 10_000_000:
                atts.append(InboundAttachment(data, ctype, part.get_filename() or ""))
    in_reply_to = str(msg.get("In-Reply-To", "") or "").strip() or None
    return {"from_email": norm_email(addr), "from_name": (name or "").strip() or None,
            "to": [t for t in (norm_email(x) for x in tos) if t], "subject": str(msg.get("Subject", "") or "").strip(),
            "text": text or "", "message_id": str(msg.get("Message-ID", "") or "").strip() or None, "in_reply_to": in_reply_to,
            "refs": list(dict.fromkeys((str(msg.get("References", "") or "") + " " + (in_reply_to or "")).split())),
            "attachments": atts, "automatic": automatic(msg)}


# ---------------------------------------------------------------------------
# Which business?
# ---------------------------------------------------------------------------
async def _active_tenants() -> list[str]:
    async with system_session() as db:
        return [str(t["id"]) for t in await db.all("SELECT id FROM tenants WHERE status='active'")]


async def route(e: dict) -> str | None:
    if e["refs"]:  # a reply to an email we sent: the business that sent it
        for tid in await _active_tenants():
            async with tenant_session(tid) as db:
                if await db.val("SELECT 1 FROM messages WHERE channel='email' AND direction='out' AND external_id = ANY(:ids) LIMIT 1", ids=e["refs"]):
                    return tid
    async with system_session() as db:  # written to an address a business receives on
        for to in e["to"]:
            if tid := await db.val("SELECT tenant_id FROM channel_routes WHERE channel='email' AND external_key=:k", k=to):
                return str(tid)
    if tid := await routing.business_for(email=e["from_email"]):  # someone a business already knows
        return tid
    listed = []  # a new customer: the business that lists this mailbox as where it receives email, if only one does
    for tid in await _active_tenants():
        async with tenant_session(tid) as db:
            cfg = await db.val("SELECT config FROM integrations WHERE kind='email'")
        if norm_email(unseal(cfg or {}).get("inbound_address")) in {mailbox(), *e["to"]}:
            listed.append(tid)
    return listed[0] if len(listed) == 1 else None


# ---------------------------------------------------------------------------
async def handle(raw: bytes) -> bool:
    from app.pipeline import InboundMessage, ingest

    e = parse(raw)
    sender = e["from_email"] or ""
    if e["automatic"] or not sender or sender == mailbox() or SKIP_SENDERS.match(sender.split("@")[0]):
        log.info("email from %s skipped (automatic or our own)", sender or "?")
        return False
    tid = await route(e)
    if not tid:
        log.info("email from %s skipped: no business knows this sender", sender)
        return False
    body = strip_quoted(e["text"]) or e["text"].strip()
    await ingest(tid, InboundMessage(
        channel="email", text=(f"Subject: {e['subject']}\n\n" + body).strip(), from_email=sender, from_name=e["from_name"],
        external_id=e["message_id"], thread_id=e["in_reply_to"] or e["message_id"], subject=e["subject"], attachments=e["attachments"]))
    log.info("email from %s → ticket for business %s", sender, tid)
    return True


async def poll_once() -> int:
    box = mailbox()
    async with system_session() as db:
        cur = await db.one("SELECT uid_validity, last_uid FROM mailbox_cursors WHERE mailbox=:m", m=box)
    validity, last, items = await asyncio.to_thread(_fetch, cur and cur["last_uid"], cur and cur["uid_validity"])
    done = 0
    for uid, raw in items:
        try:
            done += await handle(raw)
        except Exception:  # one bad email must not block the rest (ingest skips repeats by Message-ID if we see it again)
            log.exception("email: message %s failed", uid)
    async with system_session() as db:
        await db.exec("INSERT INTO mailbox_cursors (mailbox, uid_validity, last_uid) VALUES (:m,:v,:u) "
                      "ON CONFLICT (mailbox) DO UPDATE SET uid_validity=:v, last_uid=:u, updated_at=now()", m=box, v=validity, u=last)
    return done


async def poll_forever() -> None:
    log.info("email: reading %s every %ss", mailbox(), settings.email_poll_seconds)
    failures = 0
    while True:
        try:
            await poll_once()
            failures = 0
        except imaplib.IMAP4.error as e:  # wrong app password, IMAP turned off…
            failures += 1
            log.warning("email: couldn't read %s: %s", mailbox(), e)
        except Exception:
            failures += 1
            log.exception("email: poll failed")
        await asyncio.sleep(settings.email_poll_seconds * min(2 ** failures, 30))

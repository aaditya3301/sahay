"""Shared helpers: media storage, auth tokens, background jobs, audit, misc."""

import asyncio
import hashlib
import io
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Query

from app.config import settings

log = logging.getLogger("sahay")


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Media storage (GCS)
# ---------------------------------------------------------------------------
_gcs = None


def _bucket():
    global _gcs
    if _gcs is None:
        from google.cloud import storage

        _gcs = storage.Client(project=settings.gcp_project).bucket(settings.media_bucket)
    return _gcs


async def put_media(tenant_id: str, data: bytes, mime: str, name: str | None = None) -> str:
    key = f"{tenant_id}/{name or new_id()}"

    def _up():
        _bucket().blob(key).upload_from_string(data, content_type=mime)

    await asyncio.to_thread(_up)
    return f"gs://{settings.media_bucket}/{key}"


async def get_media(uri: str) -> bytes:
    bucket, _, key = uri.removeprefix("gs://").partition("/")

    def _down():
        from google.cloud import storage

        return storage.Client(project=settings.gcp_project).bucket(bucket).blob(key).download_as_bytes()

    return await asyncio.to_thread(_down)


def sniff_mime(data: bytes, default: str = "application/octet-stream") -> str:
    if data[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return "image/jpeg"
    if data[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return default


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def phash(data: bytes) -> str | None:
    try:
        import imagehash
        from PIL import Image

        return str(imagehash.phash(Image.open(io.BytesIO(data)).convert("RGB")))
    except Exception:
        return None


def phash_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def make_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": now() + timedelta(hours=settings.jwt_ttl_hours)}, settings.jwt_secret, "HS256"
    )


def read_token(token: str) -> str:
    try:
        return jwt.decode(token, settings.jwt_secret, ["HS256"])["sub"]
    except Exception:
        raise HTTPException(401, "invalid token")


class Ctx:
    def __init__(self, user_id: str, tenant_id: str, role: str, user_name: str):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.user_name = user_name


async def current_ctx(
    authorization: str | None = Header(None),
    x_tenant_id: str | None = Header(None),
    token: str | None = Query(None),
    tenant: str | None = Query(None),
) -> Ctx:
    from app.db import system_session

    raw = (authorization or "").removeprefix("Bearer ").strip() or token
    if not raw:
        raise HTTPException(401, "missing token")
    uid = read_token(raw)
    tid = x_tenant_id or tenant
    async with system_session() as db:
        if tid:
            row = await db.one(
                "SELECT m.role, u.name FROM tenant_members m JOIN users u ON u.id=m.user_id "
                "WHERE m.user_id=:u AND m.tenant_id=:t",
                u=uid, t=tid,
            )
        else:
            row = await db.one(
                "SELECT m.role, u.name, m.tenant_id FROM tenant_members m JOIN users u ON u.id=m.user_id "
                "WHERE m.user_id=:u ORDER BY m.created_at LIMIT 1",
                u=uid,
            )
            if row:
                tid = str(row["tenant_id"])
    if not row:
        raise HTTPException(403, "not a member of this workspace")
    return Ctx(uid, str(tid), row["role"], row["name"])


CtxDep = Depends(current_ctx)


# ---------------------------------------------------------------------------
# Background jobs (in-process; Cloud Run runs with CPU always allocated)
# ---------------------------------------------------------------------------
_tasks: set[asyncio.Task] = set()
# Separate lanes so a big lead search or campaign never delays a customer's support reply.
QUEUES = {"support": 8, "sales": 3, "lifecycle": 2, "knowledge": 2}
_sems: dict[str, asyncio.Semaphore] = {}


def enqueue(fn: Callable[..., Awaitable[Any]], *args: Any, delay: float = 0, name: str = "job", queue: str = "support",
            **kwargs: Any) -> None:
    sem = _sems.setdefault(queue, asyncio.Semaphore(QUEUES.get(queue, 2)))

    async def runner():
        if delay:
            await asyncio.sleep(delay)
        async with sem:
            try:
                await fn(*args, **kwargs)
            except Exception:
                log.exception("job %s failed", name)

    t = asyncio.get_event_loop().create_task(runner())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# ---------------------------------------------------------------------------
# Audit + security events
# ---------------------------------------------------------------------------
async def audit(db, actor_type: str, actor: str | None, verb: str, object_type: str, object_id: Any, data: dict | None = None):
    await db.exec(
        "INSERT INTO audit_log (tenant_id, actor_type, actor, verb, object_type, object_id, data) "
        "VALUES (:t, :at, :a, :v, :ot, :oid, :d)",
        t=db.tenant_id, at=actor_type, a=actor, v=verb, ot=object_type, oid=str(object_id), d=data or {},
    )


async def security_event(db, kind: str, detail: str, ticket_id: Any = None, severity: str = "medium", data: dict | None = None):
    await db.exec(
        "INSERT INTO security_events (tenant_id, ticket_id, kind, severity, detail, data) VALUES (:t, :tk, :k, :s, :d, :x)",
        t=db.tenant_id, tk=ticket_id, k=kind, s=severity, d=detail, x=data or {},
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def norm_phone(p: str | None) -> str | None:
    if not p:
        return None
    # Indian caller ID often arrives as 0XXXXXXXXXX (trunk prefix) or 0091XXXXXXXXXX (international prefix)
    digits = re.sub(r"\D", "", p).lstrip("0")
    if len(digits) == 10:
        digits = "91" + digits
    return "+" + digits if digits else None


def norm_email(e: str | None) -> str | None:
    if not e:
        return None
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", e)
    return m.group(0).lower() if m else None


def inr(paise: int | float | None) -> str:
    return f"₹{(paise or 0) / 100:,.0f}"


def levenshtein_ratio(a: str, b: str) -> float:
    import difflib

    return 1 - difflib.SequenceMatcher(None, a, b).ratio()


def jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "tolist"):
        return v.tolist()
    return v


# ---------------------------------------------------------------------------
# Store credentials at rest: secret-looking integration fields are encrypted (Fernet, key derived from JWT_SECRET)
# ---------------------------------------------------------------------------
SECRET_HINTS = ("token", "key", "secret", "password")


def _fernet():
    import base64

    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"{settings.jwt_secret}|integrations".encode()).digest()))


def seal(cfg: dict) -> dict:
    out = {}
    for k, v in (cfg or {}).items():
        if isinstance(v, str) and v and any(h in k for h in SECRET_HINTS) and not v.startswith("enc:"):
            v = "enc:" + _fernet().encrypt(v.encode()).decode()
        out[k] = v
    return out


def unseal(cfg: dict) -> dict:
    out = {}
    for k, v in (cfg or {}).items():
        if isinstance(v, str) and v.startswith("enc:"):
            try:
                v = _fernet().decrypt(v[4:].encode()).decode()
            except Exception:
                v = ""
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Tickets: each business has its own numbering (LL-1042)
# ---------------------------------------------------------------------------
async def next_ticket_number(db) -> int:
    return await db.val("UPDATE tenant_settings SET ticket_seq = ticket_seq + 1 RETURNING ticket_seq")


def ticket_ref(prefix: str | None, number: int | None) -> str:
    return f"{prefix or 'T'}-{number}"

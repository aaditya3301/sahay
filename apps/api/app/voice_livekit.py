"""LiveKit + Sarvam voice, the API side. Our worker (voice_agent.py) does the ears and mouth (Sarvam Saaras STT,
Bulbul TTS) and streams Gemini's words; this platform stays the brain (business data, tools, policy gate, timeline).

  browser test call → we queue the call, dispatch the agent into room sahay-<call_id>, hand the browser a join token
  outbound phone    → we dispatch the agent with the number; it dials through the LiveKit outbound SIP trunk
  inbound phone     → a LiveKit SIP dispatch rule sends the agent in; the dialled number picks the business

The worker then calls (see routes_widget.py):
  POST /v1/voice/livekit/session      prompt, tools, caller context, voice settings and a per-call token
  POST /v1/voice/livekit/tool         one tool through the policy gate, with a short line to speak
  POST /v1/voice/livekit/transcript   live transcript for the Calls page
  POST /v1/voice/livekit/end          transcript → summary, ticket, customer timeline (or why the call failed)
"""

import json
import logging
import time
from datetime import timedelta

import jwt
from livekit import api as lk

from app.config import settings
from app.core import norm_phone, now
from app.db import tenant_session

log = logging.getLogger("sahay.livekit")
ROOM_PREFIX = "sahay-"
# bulbul:v3 voices; a voice setting naming one of these picks it, otherwise "male" → rahul, anything else → priya
SPEAKERS = ["priya", "simran", "ritu", "pooja", "kavya", "ishita", "shreya", "neha", "roopa", "shruti",
            "rahul", "shubh", "amit", "ratan", "rohan", "dev", "manan", "sumit", "aditya", "kabir", "varun"]


def configured() -> bool:
    return bool(settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret)


def _api() -> lk.LiveKitAPI:
    return lk.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)


def call_token(tenant_id: str, call_id: str) -> str:
    """What the worker sends back to us for one call: it can run tools on that call and nothing else."""
    return jwt.encode({"typ": "lk_call", "t": tenant_id, "c": call_id, "exp": int(time.time()) + 4 * 3600}, settings.jwt_secret, "HS256")


def read_call_token(token: str | None) -> tuple[str, str] | None:
    try:
        d = jwt.decode(token or "", settings.jwt_secret, ["HS256"])
        return (d["t"], d["c"]) if d.get("typ") == "lk_call" else None
    except Exception:
        return None


def voice_config(prof: dict, sarvam_cfg: dict) -> dict:
    v = prof["voice"]
    lang = v.get("language") or "hinglish"
    name = (v.get("voice") or "").lower()
    speaker = next((s for s in SPEAKERS if s in name), None) or ("rahul" if "male" in name.replace("female", "") else "priya")
    return {
        # codemix: Hindi in Devanagari, English words in Latin, which is how Hinglish callers actually talk
        "stt": {"model": "saaras:v4", "language": "en-IN" if lang == "en" else "hi-IN", "mode": "codemix" if lang == "hinglish" else "transcribe"},
        "tts": {"model": "bulbul:v3", "target_language_code": "en-IN" if lang == "en" else "hi-IN", "speaker": speaker,
                "pace": float(v.get("pace") or 1.0)},
        "sarvam_api_key": sarvam_cfg.get("api_key") or "",
    }


async def _trunk(tenant_id: str) -> str:
    from app.channels import integration

    async with tenant_session(tenant_id) as db:
        cfg = await integration(db, "livekit")
    return cfg.get("sip_trunk_id") or settings.livekit_sip_trunk_id


async def _dispatch(tenant_id: str, call_id: str, metadata: dict) -> str:
    room = f"{ROOM_PREFIX}{call_id}"
    try:
        async with _api() as lkapi:  # the room is created by the dispatch
            await lkapi.agent_dispatch.create_dispatch(lk.CreateAgentDispatchRequest(
                agent_name=settings.livekit_agent_name, room=room, metadata=json.dumps(metadata)))
    except Exception as e:
        log.warning("livekit dispatch failed for %s: %s", call_id, e)
        async with tenant_session(tenant_id) as db:
            await db.exec("UPDATE voice_calls SET status='failed', ended_at=now(), context = context || :x WHERE id=:id",
                          x={"error": str(e)[:300]}, id=call_id)
        status = getattr(e, "status", None)
        if status in (401, 403):
            raise ValueError("LiveKit rejected the request: check LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET "
                             "(they must all come from the same LiveKit project)") from e
        raise ValueError(f"Couldn't reach LiveKit at {settings.livekit_url}: {str(e)[:160]}") from e
    return room


# ---------------------------------------------------------------------------
async def queue_call(tenant_id: str, *, direction: str, purpose: str, phone: str | None, test: bool, topic: str = "") -> str:
    async with tenant_session(tenant_id) as db:
        cust = await db.one("SELECT customer_id FROM customer_identities WHERE kind='phone' AND value=:p", p=norm_phone(phone)) if phone else None
        row = await db.one(
            "INSERT INTO voice_calls (tenant_id, direction, purpose, from_number, to_number, customer_id, status, context, scheduled_for) "
            "VALUES (:t,:d,:pu,:f,:to,:c,'queued',:x, now()) RETURNING id",
            t=tenant_id, d=direction, pu=purpose, f=norm_phone(phone) if direction == "inbound" else None,
            to=norm_phone(phone) if direction == "outbound" else None, c=cust and cust["customer_id"],
            x={"test": test, "provider": "livekit", **({"topic": topic} if topic else {})})
    return str(row["id"])


async def web_call(tenant_id: str, *, phone: str | None, purpose: str, identity: str, name: str) -> dict:
    """A test call from the dashboard: the browser is the caller, on the real Sarvam voice."""
    if not configured():
        raise ValueError("LiveKit isn't configured (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)")
    call_id = await queue_call(tenant_id, direction="inbound", purpose=purpose, phone=phone, test=True)
    room = await _dispatch(tenant_id, call_id, {"mode": "web", "call_id": call_id, "call_token": call_token(tenant_id, call_id)})
    token = (lk.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
             .with_identity(identity).with_name(name).with_ttl(timedelta(minutes=30))
             .with_grants(lk.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True))
             .to_jwt())
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id", x={"room": room}, id=call_id)
    return {"call_id": call_id, "url": settings.livekit_url, "token": token, "room": room}


async def place_call(tenant_id: str, call_id: str) -> dict:
    """Dial a queued outbound call (COD confirmation, callback, check-in) through the LiveKit SIP trunk."""
    trunk = await _trunk(tenant_id)
    if not configured() or not trunk:
        raise ValueError("LiveKit calling isn't set up (LiveKit keys and an outbound SIP trunk are needed)")
    async with tenant_session(tenant_id) as db:
        call = await db.one("SELECT id, to_number, status FROM voice_calls WHERE id=:id", id=call_id)
    if not call or not call["to_number"]:
        raise ValueError("call not found or has no number")
    room = await _dispatch(tenant_id, call_id, {"mode": "sip_out", "call_id": call_id, "call_token": call_token(tenant_id, call_id),
                                     "phone": call["to_number"], "trunk": trunk})
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET status='ringing', context = context || :x WHERE id=:id",
                      x={"provider": "livekit", "room": room}, id=call_id)
    return {"call_id": call_id, "room": room}


async def queue_and_dial(tenant_id: str, phone: str, purpose: str = "check_in") -> dict:
    call_id = await queue_call(tenant_id, direction="outbound", purpose=purpose, phone=phone, test=False, topic="test call from the dashboard")
    return await place_call(tenant_id, call_id)


# ---------------------------------------------------------------------------
async def session(tenant_id: str, call_id: str | None, *, from_number: str | None, to_number: str | None) -> dict:
    """Everything the worker needs before saying hello. call_id is None for an inbound phone call."""
    from app import voice
    from app.channels import integration
    from app.profile import load_profile

    direction, purpose, test = "inbound", "inbound", False
    if call_id:
        async with tenant_session(tenant_id) as db:
            row = await db.one("SELECT * FROM voice_calls WHERE id=:id", id=call_id)
        if not row:
            raise ValueError("unknown call")
        direction, purpose, test = row["direction"], row["purpose"], bool((row["context"] or {}).get("test"))
        from_number, to_number = row["from_number"], row["to_number"]
    started = await voice.start_call(tenant_id, direction=direction, from_number=from_number, to_number=to_number, purpose=purpose,
                                     call_id=call_id, test=test, stream=True)
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET context = context || :x WHERE id=:id", x={"provider": "livekit"}, id=started["call_id"])
        prof = await load_profile(db)
        sarvam_cfg = await integration(db, "sarvam")
    return {**started, "call_token": call_token(tenant_id, started["call_id"]), "voice": voice_config(prof, sarvam_cfg),
            "transfer_number": prof["voice"].get("transfer_number") or None, "language": prof["voice"].get("language") or "hinglish"}


async def tool(tenant_id: str, call_id: str, name: str, args: dict) -> dict:
    from app import voice
    from app.voice_sarvam import speakable

    out = await voice.run_tool(tenant_id, call_id, name, args)
    return {"result": speakable(name, out), "data": json.loads(json.dumps(out, default=str))}


async def live_transcript(tenant_id: str, call_id: str, transcript: list[dict]) -> None:
    async with tenant_session(tenant_id) as db:
        await db.exec("UPDATE voice_calls SET transcript=:tr WHERE id=:id AND status='in_progress'", tr=transcript[-200:], id=call_id)


async def end(tenant_id: str, call_id: str, transcript: list[dict] | None, status: str, error: str | None) -> dict:
    from app import voice

    async with tenant_session(tenant_id) as db:
        row = await db.one("SELECT status, conversation_id FROM voice_calls WHERE id=:id", id=call_id)
        if not row:
            raise ValueError("unknown call")
        if not row["conversation_id"]:  # never connected (no answer, busy, trunk error): nothing to summarise
            await db.exec("UPDATE voice_calls SET status=:st, ended_at=now(), outcome='no_answer', context = context || :x WHERE id=:id",
                          st=status if status in ("no_answer", "failed") else "no_answer", x={"error": error or "", "ended": str(now())}, id=call_id)
            return {"outcome": "no_answer", "summary": error or "The call didn't connect."}
    if row["status"] in ("completed", "no_answer", "failed"):
        return {"outcome": "already_ended"}
    return await voice.end_call(tenant_id, call_id, [{"role": m["role"], "text": m["text"]} for m in transcript or [] if m.get("text")] or None,
                                "completed" if status == "completed" else status)

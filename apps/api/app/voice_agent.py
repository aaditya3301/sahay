"""Sahay's LiveKit voice worker: Sarvam does the ears and mouth (Saaras STT, Bulbul TTS), Gemini on Vertex streams
the words, and Sahay's API stays the brain (caller context, tools, policy gate, summary, timeline).

    python -m app.voice_agent dev      # local, reloads on change
    python -m app.voice_agent start    # production; the container starts it next to the API when LIVEKIT_URL is set

One job per call. The dispatch metadata says which call (see voice_livekit.py):
  {"mode": "web", "call_id", "call_token"}                        browser test call from the dashboard
  {"mode": "sip_out", "call_id", "call_token", "phone", "trunk"}  we dial the customer through the outbound SIP trunk
  no metadata (a SIP dispatch rule)                               inbound phone call; the dialled number picks the business

Keep this module free of database imports: it talks to the API over HTTP, so it can also run on LiveKit Cloud.
"""

import asyncio
import json
import logging
import os
import time

import httpx
from livekit import api, rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, RunContext, ToolError, cli, function_tool, room_io
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import google, sarvam

from app.config import settings

log = logging.getLogger("sahay.voice_agent")
server = AgentServer(port=int(os.environ.get("VOICE_AGENT_HEALTH_PORT", "8081")))

FILLER = {"en": "One second, let me check.", "hi": "एक second, check कर रही हूँ।"}


class SahayAPI:
    def __init__(self, token: str | None):
        base = settings.voice_api_url or f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"
        self.http = httpx.AsyncClient(base_url=base.rstrip("/"), timeout=25)
        self.token = token

    async def post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        h = {**({"Authorization": f"Bearer {self.token}"} if self.token else {}), **(headers or {})}
        r = await self.http.post(path, json=body, headers=h)
        r.raise_for_status()
        return r.json()


class SahayAgent(Agent):
    def __init__(self, greeting: str, **kw):
        super().__init__(**kw)
        self.greeting = greeting

    async def on_enter(self) -> None:
        self.session.say(self.greeting)


def _tool(sahay: SahayAPI, spec: dict, ctx: JobContext, caller: rtc.RemoteParticipant, filler: str):
    name = spec["name"]

    async def handler(raw_arguments: dict, context: RunContext):
        try:
            async with context.with_filler(filler, delay=1.5):  # only speaks if the tool is slow
                out = await sahay.post("/v1/voice/livekit/tool", {"name": name, "args": raw_arguments or {}})
        except httpx.HTTPError as e:
            log.warning("tool %s failed: %s", name, e)
            raise ToolError("That system isn't responding right now; offer a callback from the team.") from e
        to = (out.get("data") or {}).get("transfer_to")
        if name == "transfer_to_human" and to and caller.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            handle = context.speech_handle

            async def transfer() -> None:
                await handle.wait_for_playout()  # let the agent finish "connecting you now" first
                try:  # cold transfer (SIP REFER): the caller leaves the room and rings the teammate
                    await ctx.api.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                        room_name=ctx.room.name, participant_identity=caller.identity, transfer_to=f"tel:{to}", play_dialtone=True))
                except Exception as e:
                    log.warning("transfer failed: %s", e)
                    context.session.say("Sorry, the transfer didn't go through. A teammate will call you back shortly.")

            asyncio.create_task(transfer())
        return out["result"]

    params = spec.get("parameters") or {"type": "object", "properties": {}}
    return function_tool(handler, raw_schema={"name": name, "description": spec["description"], "parameters": params})


def _llm() -> google.LLM:
    kw = {"vertexai": True, "project": settings.gcp_project, "location": settings.vertex_location} if settings.gcp_project else {}
    return google.LLM(model=settings.model_voice, temperature=0.3, thinking_config={"thinking_level": "minimal"}, **kw)


@server.rtc_session(agent_name=settings.livekit_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    mode = meta.get("mode") or "sip_in"
    sahay = SahayAPI(meta.get("call_token"))
    await ctx.connect()

    if mode == "sip_out":
        try:
            await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                room_name=ctx.room.name, sip_trunk_id=meta["trunk"], sip_call_to=meta["phone"],
                participant_identity=f"sip-{meta['phone']}", wait_until_answered=True))
        except api.SipCallError as e:
            status = "no_answer" if e.sip_status_code in (408, 480, 486, 487, 603) else "failed"
            log.info("outbound call %s: %s %s", meta.get("call_id"), e.sip_status_code, e.sip_status)
            await sahay.post("/v1/voice/livekit/end", {"status": status, "error": f"SIP {e.sip_status_code} {e.sip_status}"})
            ctx.shutdown("dial failed")
            return

    caller = await ctx.wait_for_participant()
    if mode == "sip_in":
        attrs = caller.attributes
        s = await sahay.post("/v1/voice/livekit/session",
                             {"from_number": attrs.get("sip.phoneNumber"), "to_number": attrs.get("sip.trunkPhoneNumber")},
                             headers={"x-platform-key": settings.voice_platform_key})
    else:
        s = await sahay.post("/v1/voice/livekit/session", {})
    sahay.token = s["call_token"]
    v = s["voice"]
    key = v.get("sarvam_api_key") or settings.sarvam_api_key or None

    transcript: list[dict] = []
    ended = asyncio.Event()

    session = AgentSession(
        stt=sarvam.STT(model=v["stt"]["model"], language=v["stt"]["language"], mode=v["stt"]["mode"], api_key=key),
        llm=_llm(),
        tts=sarvam.TTS(model=v["tts"]["model"], target_language_code=v["tts"]["target_language_code"], speaker=v["tts"]["speaker"],
                       pace=v["tts"]["pace"], api_key=key),
        # Sarvam's STT has its own VAD and end-of-speech signal; a second VAD would disagree about where turns end
        vad=None,
        turn_handling={"turn_detection": "stt", "endpointing": {"min_delay": 0.07}},
        max_tool_steps=5,
    )

    @session.on("conversation_item_added")
    def _added(ev) -> None:
        item = ev.item
        if getattr(item, "type", None) != "message" or item.role not in ("user", "assistant") or not item.text_content:
            return
        transcript.append({"role": "caller" if item.role == "user" else "agent", "text": item.text_content, "at": time.time()})
        asyncio.create_task(_push())

    async def _push() -> None:
        try:
            await sahay.post("/v1/voice/livekit/transcript", {"transcript": transcript})
        except Exception:
            pass  # live view only; the full transcript goes with /end

    async def _finish() -> None:
        if ended.is_set():
            return
        ended.set()
        try:
            await sahay.post("/v1/voice/livekit/end", {"transcript": transcript, "status": "completed"})
        except Exception as e:
            log.warning("end-of-call save failed for %s: %s", s["call_id"], e)

    @session.on("close")
    def _closed(_ev) -> None:
        asyncio.create_task(_finish())

    ctx.add_shutdown_callback(_finish)

    filler = FILLER["en" if s.get("language") == "en" else "hi"]
    tools = [_tool(sahay, spec, ctx, caller, filler) for spec in s["tools"]]
    tools.append(EndCallTool(extra_description="Use it after saying goodbye, or if the caller asks to hang up.",
                             end_instructions="say a short warm goodbye", ignore_on_enter=True))
    await session.start(room=ctx.room, agent=SahayAgent(s["greeting"], instructions=s["system_prompt"], tools=tools),
                        room_options=room_io.RoomOptions(participant_identity=caller.identity, delete_room_on_close=True))


if __name__ == "__main__":
    cli.run_app(server)

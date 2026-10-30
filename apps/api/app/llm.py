"""Gemini on Vertex AI: structured output, tool calling, embeddings, cost logging."""

import asyncio
import json
import logging
import time
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger("sahay.llm")
T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None
_embed_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=settings.gcp_project, location=settings.vertex_location)
    return _client


def embed_client() -> genai.Client:
    global _embed_client
    if _embed_client is None:
        _embed_client = genai.Client(vertexai=True, project=settings.gcp_project, location=settings.embed_location)
    return _embed_client


def _cost_micros(model: str, usage) -> tuple[int, int, int]:
    if not usage:
        return 0, 0, 0
    tin = usage.prompt_token_count or 0
    tout = (usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
    pi, po = settings.prices.get(model, (0.5, 3.0))
    usd = tin / 1e6 * pi + tout / 1e6 * po
    return tin, tout, int(usd * settings.usd_inr * 1e6)  # micro-rupees


async def _log_call(tenant_id: str | None, purpose: str, model: str, usage, latency_ms: int, status: str):
    if not tenant_id:
        return
    from app.db import tenant_session

    tin, tout, cost = _cost_micros(model, usage)
    try:
        async with tenant_session(tenant_id) as db:
            await db.exec(
                "INSERT INTO llm_calls (tenant_id, purpose, model, input_tokens, output_tokens, cost_micros, latency_ms, status) "
                "VALUES (:t, :p, :m, :i, :o, :c, :l, :s)",
                t=tenant_id, p=purpose, m=model, i=tin, o=tout, c=cost, l=latency_ms, s=status,
            )
    except Exception:  # cost logging must never break the pipeline
        log.exception("llm_calls insert failed")


async def generate(
    *,
    model: str,
    contents: Any,
    system: str | None = None,
    tools: list[types.Tool] | None = None,
    schema: type[BaseModel] | None = None,
    temperature: float = 0.2,
    purpose: str = "generic",
    tenant_id: str | None = None,
    thinking: str | None = None,
    retries: int = 3,
) -> types.GenerateContentResponse:
    cfg: dict[str, Any] = {"temperature": temperature}
    if system:
        cfg["system_instruction"] = system
    if tools:
        cfg["tools"] = tools
        cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
    if schema:
        cfg["response_mime_type"] = "application/json"
        cfg["response_json_schema"] = schema.model_json_schema()
    models = [model] + ([settings.model_fallback] if settings.model_fallback != model else [])
    if thinking:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level=thinking)
    last: Exception | None = None
    for m in models:
        for attempt in range(retries):
            t0 = time.perf_counter()
            try:
                resp = await client().aio.models.generate_content(
                    model=m, contents=contents, config=types.GenerateContentConfig(**cfg)
                )
                await _log_call(tenant_id, purpose, m, resp.usage_metadata, int((time.perf_counter() - t0) * 1000), "ok")
                return resp
            except Exception as e:  # noqa: BLE001
                last = e
                msg = str(e)
                if "thinking" in msg.lower() and "thinking_config" in cfg:
                    cfg.pop("thinking_config")  # model doesn't support the level; retry without
                    continue
                log.warning("gemini %s failed (attempt %s): %s", m, attempt + 1, msg[:300])
                quota = "429" in msg or "RESOURCE_EXHAUSTED" in msg
                await asyncio.sleep((4.0 if quota else 0.8) * (attempt + 1))
        await _log_call(tenant_id, purpose, m, None, 0, "error")
        cfg.pop("thinking_config", None)
    raise RuntimeError(f"Gemini call failed: {last}")


async def structured(
    schema: type[T],
    *,
    model: str,
    contents: Any,
    system: str | None = None,
    temperature: float = 0.1,
    purpose: str = "structured",
    tenant_id: str | None = None,
    thinking: str | None = None,
) -> T:
    resp = await generate(
        model=model, contents=contents, system=system, schema=schema, temperature=temperature,
        purpose=purpose, tenant_id=tenant_id, thinking=thinking,
    )
    text = resp.text or "{}"
    try:
        return schema.model_validate_json(text)
    except Exception:
        # tolerate fenced or slightly wrapped JSON
        start, end = text.find("{"), text.rfind("}")
        return schema.model_validate(json.loads(text[start : end + 1]))


async def text(
    *, model: str, contents: Any, system: str | None = None, temperature: float = 0.4,
    purpose: str = "text", tenant_id: str | None = None,
) -> str:
    resp = await generate(model=model, contents=contents, system=system, temperature=temperature,
                          purpose=purpose, tenant_id=tenant_id)
    return (resp.text or "").strip()


async def embed(texts: list[str], task: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 50):
        batch = [t[:8000] or " " for t in texts[i : i + 50]]
        for attempt in range(3):
            try:
                r = await embed_client().aio.models.embed_content(
                    model=settings.model_embed,
                    contents=batch,
                    config=types.EmbedContentConfig(task_type=task, output_dimensionality=settings.embed_dims),
                )
                out.extend([list(e.values) for e in r.embeddings])
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
    return out


async def embed_one(text_: str, task: str = "RETRIEVAL_QUERY") -> list[float]:
    return (await embed([text_], task))[0]


def part_bytes(data: bytes, mime: str) -> types.Part:
    return types.Part.from_bytes(data=data, mime_type=mime)

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.config import ROOT, settings
from app.routes_dash import router as dash_router
from app.routes_api import router as api_router
from app.routes_public import router as public_router
from app.routes_sales import router as sales_router
from app.routes_widget import router as widget_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request lines would print bot tokens that live in URLs (Telegram)

app = FastAPI(title="Sahay API", version="0.1.0", docs_url="/api-docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(public_router)
app.include_router(api_router)
app.include_router(widget_router)
app.include_router(dash_router)
app.include_router(sales_router)

WEB_DIST = ROOT / "apps" / "web" / "dist"
DOCS_DIR = ROOT / "apps" / "docs"


@app.on_event("startup")
async def warm_up():
    """The first Gemini call in a process pays ~10s for auth + connection setup; do it before the first caller does."""
    import asyncio

    from app import llm

    async def ping():
        try:
            await llm.generate(model=settings.model_voice, contents="ok", purpose="warmup", retries=1)
        except Exception:
            pass

    asyncio.get_event_loop().create_task(ping())
    from app import telegram

    if telegram.enabled() and settings.telegram_polling:
        asyncio.get_event_loop().create_task(telegram.poll_forever())


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/health")
async def healthz():
    return {"ok": True, "app": settings.app_name}


@app.get("/docs", include_in_schema=False)
@app.get("/docs/{name}", include_in_schema=False)
async def developer_docs(name: str = "index.html"):
    f = DOCS_DIR / name
    if f.parent != DOCS_DIR or not f.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith(("v1/", "webhooks/", "media/", "public/", "internal/", "w/", "r/", "u/")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    f = WEB_DIST / path
    if path and f.is_file():
        return FileResponse(f)
    index = WEB_DIST / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Dashboard not built. Run `npm run build` in apps/web."}, status_code=404)

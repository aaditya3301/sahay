"""Knowledge base: ingest → chunk → embed → hybrid search (pgvector + full text, RRF)."""

import hashlib
import html
import io
import logging
import re

import httpx

from app import llm
from app.config import settings

log = logging.getLogger(__name__)


def chunk_markdown(md: str, target_chars: int = 1800, overlap: int = 200) -> list[tuple[str, str]]:
    """Split by headings, then by size. Returns (heading_path, text)."""
    sections: list[tuple[list[str], list[str]]] = []
    path: list[str] = []
    buf: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if buf:
                sections.append((path.copy(), buf))
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            buf = []
        else:
            buf.append(line)
    if buf:
        sections.append((path.copy(), buf))
    out: list[tuple[str, str]] = []
    for p, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        hp = " > ".join(p)
        while len(body) > target_chars:
            cut = body.rfind("\n", 0, target_chars)
            cut = cut if cut > target_chars // 2 else target_chars
            out.append((hp, body[:cut].strip()))
            body = body[max(cut - overlap, 0):].strip()
        out.append((hp, body))
    return out


async def add_document(db, title: str, content_md: str, source: str, url: str | None = None) -> str:
    checksum = hashlib.sha256(content_md.encode()).hexdigest()
    existing = await db.one("SELECT id FROM kb_documents WHERE checksum=:c AND active", c=checksum)
    if existing:
        return str(existing["id"])
    if url:
        await db.exec("UPDATE kb_documents SET active=false WHERE url=:u", u=url)
    doc = await db.one(
        "INSERT INTO kb_documents (tenant_id, source, title, url, content_md, checksum) VALUES (:t,:s,:ti,:u,:c,:k) RETURNING id",
        t=db.tenant_id, s=source, ti=title, u=url, c=content_md, k=checksum,
    )
    chunks = chunk_markdown(content_md) or [(title, content_md)]
    texts = [f"{title} > {hp}\n{c}" if hp else f"{title}\n{c}" for hp, c in chunks]
    vecs = await llm.embed(texts, "RETRIEVAL_DOCUMENT")
    for i, ((hp, c), v) in enumerate(zip(chunks, vecs)):
        await db.exec(
            "INSERT INTO kb_chunks (tenant_id, document_id, ord, heading_path, content, embedding) VALUES (:t,:d,:o,:h,:c,:e)",
            t=db.tenant_id, d=doc["id"], o=i, h=f"{title} > {hp}" if hp else title, c=c, e=v,
        )
    return str(doc["id"])


async def search(db, queries: list[str], k: int | None = None) -> list[dict]:
    """Hybrid retrieval with Reciprocal Rank Fusion across all queries. RLS scopes to tenant."""
    k = k or settings.kb_top_k
    queries = [q for q in queries if q and q.strip()][:4]
    if not queries:
        return []
    vecs = await llm.embed(queries, "RETRIEVAL_QUERY")
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for q, v in zip(queries, vecs):
        vec_rows = await db.all(
            "SELECT c.id, c.content, c.heading_path, d.title, d.url, 1 - (c.embedding <=> :e) AS sim "
            "FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id WHERE d.active "
            "ORDER BY c.embedding <=> :e LIMIT 20",
            e=v,
        )
        fts_rows = await db.all(
            "SELECT c.id, c.content, c.heading_path, d.title, d.url, ts_rank(c.tsv, query) AS rank "
            "FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id, "
            "plainto_tsquery('simple', :q) query WHERE d.active AND c.tsv @@ query ORDER BY rank DESC LIMIT 20",
            q=q,
        )
        for rank, r in enumerate(vec_rows):
            if r["sim"] < 0.45:
                continue
            key = str(r["id"])
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            rows[key] = r
        for rank, r in enumerate(fts_rows):
            key = str(r["id"])
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            rows.setdefault(key, r)
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
    out = []
    for i, (cid, score) in enumerate(ranked):
        r = rows[cid]
        out.append({
            "id": cid, "ref": f"S{i + 1}", "title": r["title"], "heading": r["heading_path"], "url": r["url"],
            "content": r["content"], "score": round(score, 4), "similarity": round(float(r.get("sim") or 0), 3),
        })
    return out


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
async def fetch_url_markdown(url: str) -> tuple[str, str]:
    if settings.firecrawl_api_key:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                json={"url": url, "formats": ["markdown"]},
            )
            r.raise_for_status()
            data = r.json()["data"]
            return data.get("metadata", {}).get("title") or url, data["markdown"]
    import trafilatura

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "SahayBot/1.0"}) as c:
        r = await c.get(url)
        r.raise_for_status()
    md = trafilatura.extract(r.text, output_format="markdown", include_links=False) or ""
    title_m = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
    return (title_m.group(1).strip() if title_m else url), md


def normalize_url(raw: str) -> str:
    """Accept "beatband.in", "www.beatband.in/pages/faq" or a full URL."""
    from urllib.parse import urlparse

    u = (raw or "").strip()
    if not u:
        raise ValueError("Enter your website address, like yourstore.in")
    if not re.match(r"^[a-z][a-z\d+.-]*://", u, re.I):
        u = "https://" + u
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or "." not in parsed.netloc or parsed.username:
        raise ValueError("Enter a website address like yourstore.in")
    return u


async def _search_site_knowledge(db, root_url: str) -> list[str]:
    """Fallback for sites we can't read directly (JavaScript-only pages, bot blocking): ask Gemini with Google Search
    to collect what the public web says about this business, and keep it only if it found real content."""
    from urllib.parse import urlparse

    from google.genai import types

    host = urlparse(root_url).netloc.removeprefix("www.")
    prompt = (
        f"Use Google Search to research the online store at {host}. Write a knowledge base article in Markdown for a customer-support agent, "
        "using ONLY facts found on that website or its public listings: what they sell (product types, materials, price range), "
        "shipping, delivery times, returns/refund/exchange policy, warranty, payment options (COD, UPI), contact details and common FAQs. "
        "Use ## headings per topic. If you cannot find a topic, leave it out. Never guess or invent policies. "
        "If you find nothing about this store, reply with exactly NOT_FOUND."
    )
    resp = await llm.generate(model=settings.model_resolver, contents=prompt, temperature=0.1, purpose="kb_site_search",
                              tenant_id=db.tenant_id, tools=[types.Tool(google_search=types.GoogleSearch())])
    text = (resp.text or "").strip()
    if len(text) < 300 or "NOT_FOUND" in text[:40]:
        return []
    return [await add_document(db, f"{host} (from web search)", text, "web_search", root_url)]


async def crawl_site(db, root_url: str, max_pages: int = 15) -> list[str]:
    """Fetch the root page plus same-site links that look like help/policy pages; fall back to a Google-grounded
    summary when the pages can't be read."""
    from urllib.parse import urljoin, urlparse

    root_url = normalize_url(root_url)
    doc_ids: list[str] = []
    links = {root_url}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; SahayBot/1.0)"}) as c:
            r = await c.get(root_url)
            r.raise_for_status()
        root_url = str(r.url)  # after redirects (e.g. example.in -> www.example.in)
        host = urlparse(root_url).netloc
        links = {root_url}
        for href in re.findall(r"""href=["']([^"'#]+)["']""", r.text):
            u = urljoin(root_url, href)
            if urlparse(u).netloc == host and re.search(r"faq|help|policy|polic|shipping|return|refund|terms|about|contact|support|pages/", u, re.I):
                links.add(u.split("?")[0])
    except Exception as e:  # unreachable, blocked, or not a website: try the search fallback below
        log.warning("crawl of %s failed: %s", root_url, e)
        links = set()
    for u in list(links)[:max_pages]:
        try:
            title, md = await fetch_url_markdown(u)
            title = re.sub(r"\s+", " ", html.unescape(title)).strip()[:140] or u
            if len(md) > 200:
                doc_ids.append(await add_document(db, title, md, "url_crawl", u))
        except Exception:
            continue
    if not doc_ids:
        try:
            doc_ids = await _search_site_knowledge(db, root_url)
        except Exception as e:
            log.warning("search fallback for %s failed: %s", root_url, e)
    return doc_ids


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    return data.decode("utf-8", errors="ignore")

"""Demo storefront for a new workspace: the AI writes a small catalogue from the business description, then
product photos are generated in the background (cached in GCS like the seed images)."""

import asyncio
import logging

from pydantic import BaseModel, Field

from app import llm
from app.config import settings
from app.core import get_media, phash
from app.db import tenant_session

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()  # keep references so background image jobs aren't garbage-collected


class DemoProduct(BaseModel):
    title: str
    description: str = Field(description="One or two plain sentences a shopper would read")
    category: str
    price_inr: int = Field(description="Realistic Indian retail price in rupees")
    image_prompt: str = Field(description="What the product looks like, for a studio photo (no brand text)")


class DemoCatalog(BaseModel):
    products: list[DemoProduct]


async def generate_catalog(db, tenant_id: str) -> list[dict]:
    """Replace any earlier generated products with a fresh catalogue. Returns [{sku, title, image_prompt}]."""
    s = await db.one("SELECT s.business_description, s.ticket_prefix, t.name, t.slug FROM tenant_settings s JOIN tenants t ON t.id=s.tenant_id")
    out = await llm.structured(
        DemoCatalog, model=settings.model_resolver, purpose="demo_store", tenant_id=tenant_id, temperature=0.8,
        system="You create believable demo catalogues for Indian online stores. Never use real brand names other than the store's own.",
        contents=(f"Store: {s['name']}\nWhat they sell: {s['business_description']}\n\n"
                  "Create 8 products this store would plausibly sell. Titles are short and specific (2-5 words). Prices are realistic for India "
                  "(whole rupees, mostly between 199 and 2999). Mix price points. Each needs a short description, a category and an image_prompt "
                  "describing the physical product for a studio photo."))
    products = [p for p in out.products if p.title.strip() and p.price_inr > 0][:8]
    if not products:
        raise RuntimeError("The AI didn't return any products")
    prefix = (s["ticket_prefix"] or "DM").upper()[:3]
    await db.exec("DELETE FROM commerce_products WHERE attributes->>'generated' = 'true'")
    made = []
    for i, p in enumerate(products, 1):
        sku = f"{prefix}-{i:02d}"
        await db.exec(
            "INSERT INTO commerce_products (tenant_id, sku, title, description, category, price_paise, stock, attributes) "
            "VALUES (:t,:s,:ti,:d,:c,:p,:st,:a) ON CONFLICT (tenant_id, sku) DO UPDATE SET title=:ti, description=:d, category=:c, price_paise=:p, "
            "stock=:st, attributes=:a, image_uri=NULL, image_phash=NULL",
            t=tenant_id, s=sku, ti=p.title.strip(), d=p.description.strip(), c=p.category.strip(), p=p.price_inr * 100, st=40, a={"generated": "true"})
        made.append({"sku": sku, "title": p.title, "image_prompt": p.image_prompt})
    has_commerce = await db.val("SELECT count(*) FROM integrations WHERE kind='commerce'")
    if not has_commerce:
        await db.exec("INSERT INTO integrations (tenant_id, kind, config) VALUES (:t,'commerce',:c)", t=tenant_id, c={"provider": "mock"})
    made_slug = s["slug"]
    for m in made:
        m["slug"] = made_slug
    return made


async def _make_image(tenant_id: str, slug: str, sku: str, prompt: str) -> None:
    from app.seed import gen_image

    try:
        uri = await gen_image(slug, f"gen-{sku}", f"Professional e-commerce studio product photo of {prompt}, centered on a clean light background, soft shadows, no text")
        ph = phash(await get_media(uri))
        async with tenant_session(tenant_id) as db:
            await db.exec("UPDATE commerce_products SET image_uri=:u, image_phash=:p WHERE sku=:s", u=uri, p=ph, s=sku)
    except Exception:
        log.exception("demo image failed for %s", sku)


def start_images(tenant_id: str, products: list[dict]) -> None:
    async def run():
        sem = asyncio.Semaphore(4)

        async def one(m):
            async with sem:
                await _make_image(tenant_id, m["slug"], m["sku"], m["image_prompt"])

        await asyncio.gather(*(one(m) for m in products))

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

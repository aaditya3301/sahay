"""Screenshot dashboard pages: python shoot.py <tenant_slug> <path> [<path>...]"""
import asyncio, json, sys, urllib.request
from playwright.async_api import async_playwright
import os
BASE = os.environ.get("BASE", "http://localhost:8080")
async def main():
    slug, paths = sys.argv[1], sys.argv[2:]
    req = urllib.request.Request(BASE + "/v1/auth/login", data=json.dumps({"email": "demo@sahay.app", "password": "sahay-demo"}).encode(), headers={"content-type": "application/json"})
    r = json.load(urllib.request.urlopen(req))
    tenant = next(t["id"] for t in r["tenants"] if t["slug"] == slug)
    authv = json.dumps({"token": r["token"], "tenants": r["tenants"], "tenant": tenant, "user": r["user"]})
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="msedge")
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        await pg.goto(BASE + "/login")
        await pg.evaluate("v => localStorage.setItem('sahay.auth', v)", authv)
        for path in paths:
            await pg.goto(BASE + path); await pg.wait_for_timeout(2500)
            name = path.strip("/").replace("/", "_") or "root"
            await pg.screenshot(path=f"scripts/shots/{slug}_{name}.png", full_page=True)
            print("shot", name)
        await b.close()
asyncio.run(main())

"""Give the Telegram bot Sahay's look: profile photo from the website's logo (apps/web/public/sahay-mark.svg),
description, short description and command menu. Re-run after rotating the bot token.

    cd apps/api && uv run python ../../scripts/telegram_branding.py            # render + upload
    cd apps/api && uv run python ../../scripts/telegram_branding.py --preview  # only write scripts/out/telegram-photo.jpg
"""

import asyncio
import io
import json
import math
import re
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "apps" / "web" / "public" / "sahay-mark.svg"
OUT = ROOT / "scripts" / "out" / "telegram-photo.jpg"
SIZE, SS = 640, 4  # output pixels, supersampling
DESCRIPTION = ("Hi! I'm Sahay, the shop's support assistant. Ask about your order, delivery, returns or a damaged item, "
               "in Hindi, Hinglish or English. I'll first ask your name and order ID so only you can see your order.")
SHORT = "Help with your order: delivery, returns, damaged items. Hindi, Hinglish or English."
COMMANDS = [{"command": "start", "description": "Start: I'll ask your name and order ID"},
            {"command": "new", "description": "Switch to another order"}]


def arc_points(x1, y1, rx, ry, phi, large, sweep, x2, y2, n=48):
    """SVG elliptical arc (endpoint form) → points, per the SVG spec's centre parameterisation."""
    phi = math.radians(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = math.cos(phi) * dx + math.sin(phi) * dy
    y1p = -math.sin(phi) * dx + math.cos(phi) * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0, num / (rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2))) * (-1 if large == sweep else 1)
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = math.cos(phi) * cxp - math.sin(phi) * cyp + (x1 + x2) / 2
    cy = math.sin(phi) * cxp + math.cos(phi) * cyp + (y1 + y2) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)  # noqa: E731
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    return [(cx + rx * math.cos(t1 + dt * i / n) * math.cos(phi) - ry * math.sin(t1 + dt * i / n) * math.sin(phi),
             cy + rx * math.cos(t1 + dt * i / n) * math.sin(phi) + ry * math.sin(t1 + dt * i / n) * math.cos(phi)) for i in range(1, n + 1)]


def path_points(d: str) -> list[tuple[float, float]]:
    """Flatten the SVG path commands used by the logo (M, a/A, v, h, l, c, Z) into points."""
    toks = re.findall(r"[MmAaVvHhLlCcZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    pts, i, x, y, cmd = [], 0, 0.0, 0.0, None
    num = lambda: float(toks[i])  # noqa: E731
    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                continue
        if cmd in "Mm":
            nx, ny = float(toks[i]), float(toks[i + 1]); i += 2
            x, y = (x + nx, y + ny) if cmd == "m" else (nx, ny)
            pts.append((x, y))
        elif cmd in "Aa":
            rx, ry, phi, la, sw, ex, ey = (float(t) for t in toks[i:i + 7]); i += 7
            ex, ey = (x + ex, y + ey) if cmd == "a" else (ex, ey)
            pts += arc_points(x, y, rx, ry, phi, int(la), int(sw), ex, ey)
            x, y = ex, ey
        elif cmd in "Vv":
            v = num(); i += 1
            y = y + v if cmd == "v" else v
            pts.append((x, y))
        elif cmd in "Hh":
            v = num(); i += 1
            x = x + v if cmd == "h" else v
            pts.append((x, y))
        elif cmd in "Ll":
            nx, ny = float(toks[i]), float(toks[i + 1]); i += 2
            x, y = (x + nx, y + ny) if cmd == "l" else (nx, ny)
            pts.append((x, y))
        elif cmd in "Cc":
            c = [float(t) for t in toks[i:i + 6]]; i += 6
            if cmd == "c":
                c = [x + c[0], y + c[1], x + c[2], y + c[3], x + c[4], y + c[5]]
            for k in range(1, 25):
                t = k / 24
                pts.append(((1 - t) ** 3 * x + 3 * (1 - t) ** 2 * t * c[0] + 3 * (1 - t) * t ** 2 * c[2] + t ** 3 * c[4],
                            (1 - t) ** 3 * y + 3 * (1 - t) ** 2 * t * c[1] + 3 * (1 - t) * t ** 2 * c[3] + t ** 3 * c[5]))
            x, y = c[4], c[5]
        else:
            raise ValueError(f"unsupported path command {cmd}")
    return pts


def render() -> bytes:
    svg = SVG.read_text(encoding="utf-8")
    bg = re.search(r'<rect[^>]*fill="(#[0-9A-Fa-f]{6})"', svg).group(1)
    paths = re.findall(r'<path d="([^"]+)"([^>]*)/>', svg)
    circle = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)" fill="(#[0-9A-Fa-f]{6})"', svg)
    W = SIZE * SS
    img = Image.new("RGB", (W, W), bg)  # full square: Telegram crops it to a circle
    draw = ImageDraw.Draw(img)
    # the artwork spans roughly x 8–35, y 6–34 of the 40×40 viewBox: centre it and keep it inside the circle crop
    scale, cx, cy = W / 40 * 0.88, 21.5, 20
    tf = lambda p: (W / 2 + (p[0] - cx) * scale, W / 2 + (p[1] - cy) * scale)  # noqa: E731
    for d, attrs in paths:
        fill = re.search(r'fill="(#[0-9A-Fa-f]{6})"', attrs)
        stroke = re.search(r'stroke="(#[0-9A-Fa-f]{6})"', attrs)
        pts = [tf(p) for p in path_points(d)]
        if fill:
            draw.polygon(pts, fill=fill.group(1))
        elif stroke:
            sw = float(re.search(r'stroke-width="([\d.]+)"', attrs).group(1)) * scale
            draw.line(pts, fill=stroke.group(1), width=int(sw), joint="curve")
            for px, py in (pts[0], pts[-1]):  # round caps
                draw.ellipse((px - sw / 2, py - sw / 2, px + sw / 2, py + sw / 2), fill=stroke.group(1))
    if circle:
        ccx, ccy = tf((float(circle.group(1)), float(circle.group(2))))
        r = float(circle.group(3)) * scale
        draw.ellipse((ccx - r, ccy - r, ccx + r, ccy + r), fill=circle.group(4))
    out = io.BytesIO()
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out, "JPEG", quality=95)
    return out.getvalue()


async def main():
    jpg = render()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(jpg)
    print(f"photo: {OUT} ({len(jpg) // 1024} KB)")
    if "--preview" in sys.argv:
        return
    env = dict(re.findall(r"(?m)^([A-Z_]+)=(.*)$", (ROOT / ".env").read_text(encoding="utf-8")))
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in .env")
    base = f"https://api.telegram.org/bot{token}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{base}/setMyProfilePhoto", data={"photo": json.dumps({"type": "static", "photo": "attach://pic"})},
                         files={"pic": ("sahay.jpg", jpg, "image/jpeg")})
        print("profile photo:", r.json().get("ok"), r.json().get("description", ""))
        for method, payload in [("setMyDescription", {"description": DESCRIPTION}), ("setMyShortDescription", {"short_description": SHORT}),
                                ("setMyCommands", {"commands": COMMANDS})]:
            r = await c.post(f"{base}/{method}", json=payload)
            print(f"{method}:", r.json().get("ok"), r.json().get("description", ""))


asyncio.run(main())

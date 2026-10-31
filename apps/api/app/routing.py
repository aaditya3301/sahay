"""Which business is this person a customer of?

One central mailbox, one Telegram bot and one phone number serve every business on Sahay, so an incoming email,
chat or call is matched to the business that already knows the sender (by verified phone or email). When several
do, the one they dealt with most recently wins.
"""

from app.core import norm_email, norm_phone
from app.db import system_session, tenant_session


async def find_customer(phone: str | None = None, email: str | None = None, only: str | None = None) -> list[dict]:
    """Every business where this phone or email belongs to a customer, most recently active first:
    [{tenant_id, business, customer_id, name, last_active}]."""
    phone, email = norm_phone(phone), norm_email(email)
    if not phone and not email:
        return []
    async with system_session() as db:
        tenants = await db.all("SELECT id, name FROM tenants WHERE status='active'" + (" AND id=:t" if only else ""), **({"t": only} if only else {}))
    found = []
    for t in tenants:
        async with tenant_session(t["id"]) as db:
            row = await db.one(
                "SELECT c.id, c.display_name, greatest(c.created_at, "
                " (SELECT max(m.created_at) FROM messages m WHERE m.customer_id=c.id), "
                " (SELECT max(o.placed_at) FROM commerce_orders o WHERE o.customer_phone=:p OR lower(o.customer_email)=:e)) AS last_active "
                "FROM customer_identities i JOIN customers c ON c.id=i.customer_id "
                "WHERE NOT c.is_simulation AND ((i.kind='phone' AND i.value=:p) OR (i.kind='email' AND i.value=:e)) "
                "ORDER BY i.verified DESC, c.created_at LIMIT 1",
                p=phone or "", e=email or "",
            )
        if row:
            found.append({"tenant_id": str(t["id"]), "business": t["name"], "customer_id": str(row["id"]),
                          "name": row["display_name"], "last_active": row["last_active"]})
    return sorted(found, key=lambda f: f["last_active"], reverse=True)


async def business_for(phone: str | None = None, email: str | None = None) -> str | None:
    """The business this sender is a customer of (the most recent one), or None for someone no business knows."""
    found = await find_customer(phone=phone, email=email)
    return found[0]["tenant_id"] if found else None

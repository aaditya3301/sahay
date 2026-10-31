"""Which business is this person a customer of?

One central mailbox, one Telegram bot and one phone number serve every business on Sahay, so an incoming email,
chat or call is matched to the business that already knows the sender (by verified phone or email).

Only real customers count: someone with an order or a storefront account at the business, or who has written to it
(chat, email, Telegram). A record that a phone call alone created (any caller used to get one) doesn't make someone a
customer. When several businesses know the person, one where they have an order or an account wins, then the one
they dealt with most recently.
"""

from app.core import norm_email, norm_phone
from app.db import system_session, tenant_session


async def find_customer(phone: str | None = None, email: str | None = None, only: str | None = None) -> list[dict]:
    """Every business where this phone or email belongs to a real customer, best match first:
    [{tenant_id, business, customer_id, name, bought, last_active}]."""
    phone, email = norm_phone(phone), norm_email(email)
    if not phone and not email:
        return []
    async with system_session() as db:
        tenants = await db.all("SELECT id, name FROM tenants WHERE status='active'" + (" AND id=:t" if only else ""), **({"t": only} if only else {}))
    found = []
    for t in tenants:
        async with tenant_session(t["id"]) as db:
            row = await db.one(
                "SELECT c.id, c.display_name, b.bought, greatest(c.created_at, "
                " (SELECT max(m.created_at) FROM messages m WHERE m.customer_id=c.id), "
                " (SELECT max(o.placed_at) FROM commerce_orders o WHERE o.customer_phone=:p OR lower(o.customer_email)=:e)) AS last_active "
                "FROM customer_identities i JOIN customers c ON c.id=i.customer_id "
                # bought: an order with this phone/email, or a storefront account
                "CROSS JOIN LATERAL (SELECT (EXISTS (SELECT 1 FROM commerce_orders o WHERE o.status NOT IN ('cancelled','payment_pending') "
                "  AND (o.customer_phone=:p OR lower(o.customer_email)=:e)) OR EXISTS (SELECT 1 FROM shoppers s WHERE s.customer_id=c.id)) AS bought) b "
                "WHERE NOT c.is_simulation AND ((i.kind='phone' AND i.value=:p) OR (i.kind='email' AND i.value=:e)) "
                "AND (b.bought "
                # ... or they reached out themselves: another identity (email, Telegram, website visitor) or a non-call message
                " OR EXISTS (SELECT 1 FROM customer_identities j WHERE j.customer_id=c.id AND j.kind <> 'phone') "
                " OR EXISTS (SELECT 1 FROM messages m WHERE m.customer_id=c.id AND m.direction='in' AND m.channel <> 'voice')) "
                "ORDER BY b.bought DESC, i.verified DESC, c.created_at LIMIT 1",
                p=phone or "", e=email or "",
            )
        if row:
            found.append({"tenant_id": str(t["id"]), "business": t["name"], "customer_id": str(row["id"]),
                          "name": row["display_name"], "bought": bool(row["bought"]), "last_active": row["last_active"]})
    return sorted(found, key=lambda f: (f["bought"], f["last_active"]), reverse=True)


async def business_for(phone: str | None = None, email: str | None = None) -> str | None:
    """The business this sender is a customer of (best match), or None for someone no business knows."""
    found = await find_customer(phone=phone, email=email)
    return found[0]["tenant_id"] if found else None

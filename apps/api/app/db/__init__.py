"""Database access.

All tenant data is read and written through `tenant_session(tenant_id)`, which sets
`app.tenant_id` for the transaction so Postgres row-level security scopes every query.
`system_session()` has no tenant set: RLS returns zero rows from tenant tables, so it can
only usefully touch the global tables (tenants, users, members, channel_routes).
"""

import json
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator

from pgvector.asyncpg import register_vector
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.config import settings

_engines: dict[str, AsyncEngine] = {}
_connector = None


def _json_default(o: Any) -> Any:
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def _dumps(v: Any) -> str:
    return json.dumps(v, default=_json_default)


async def _init_conn(conn) -> None:
    await conn.set_type_codec("jsonb", encoder=_dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=_dumps, decoder=json.loads, schema="pg_catalog")
    try:
        await register_vector(conn)
    except Exception:
        pass  # extension not yet created (first migration)


def _make_engine(user: str, password: str) -> AsyncEngine:
    global _connector
    if settings.db_host:
        from urllib.parse import quote

        dsn = f"postgresql+asyncpg://{user}:{quote(password)}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        engine = create_async_engine(dsn, pool_size=5, max_overflow=5, pool_pre_ping=True)
    else:
        from google.cloud.sql.connector import Connector, create_async_connector  # noqa: F401

        async def creator():
            global _connector
            if _connector is None:
                _connector = await create_async_connector()
            return await _connector.connect_async(
                settings.db_instance, "asyncpg", user=user, password=password, db=settings.db_name
            )

        engine = create_async_engine(
            "postgresql+asyncpg://", async_creator=creator, pool_size=5, max_overflow=5, pool_pre_ping=True
        )

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _):
        dbapi_conn.run_async(_init_conn)

    return engine


def engine(owner: bool = False) -> AsyncEngine:
    key = "owner" if owner else "app"
    if key not in _engines:
        if owner:
            _engines[key] = _make_engine(settings.db_owner_user, settings.db_owner_password)
        else:
            _engines[key] = _make_engine(settings.db_user, settings.db_password)
    return _engines[key]


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "to_list"):  # pgvector Vector
        return v.to_list()
    return v


class DB:
    """Thin helper around a connection: dict rows, simple params."""

    def __init__(self, conn: AsyncConnection, tenant_id: str | None):
        self.conn = conn
        self.tenant_id = tenant_id

    async def all(self, sql: str, **params: Any) -> list[dict]:
        res = await self.conn.execute(text(sql), params)
        if not res.returns_rows:
            return []
        return [{k: _plain(v) for k, v in r._mapping.items()} for r in res]

    async def one(self, sql: str, **params: Any) -> dict | None:
        rows = await self.all(sql, **params)
        return rows[0] if rows else None

    async def val(self, sql: str, **params: Any) -> Any:
        row = await self.one(sql, **params)
        return next(iter(row.values())) if row else None

    async def exec(self, sql: str, **params: Any) -> None:
        await self.conn.execute(text(sql), params)


@asynccontextmanager
async def tenant_session(tenant_id: str | uuid.UUID) -> AsyncIterator[DB]:
    async with engine().begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        yield DB(conn, str(tenant_id))


@asynccontextmanager
async def system_session() -> AsyncIterator[DB]:
    async with engine().begin() as conn:
        yield DB(conn, None)


@asynccontextmanager
async def owner_session() -> AsyncIterator[DB]:
    async with engine(owner=True).begin() as conn:
        yield DB(conn, None)


def j(v: Any) -> Any:
    """Pass-through marker for JSON params (codec encodes dicts/lists)."""
    return v

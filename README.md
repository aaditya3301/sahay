# Sahay

AI sales and support agent for D2C brands. Sahay answers customers on website chat, Telegram, email and phone in English, Hindi and Hinglish, looks up live order data, and takes order actions (replacement, refund, cancellation, address change) under rules written by the business owner.

Built for problem statement 2.5: an AI agent for automated customer support and ticket triage.

## Features

| Requirement | Implementation |
|---|---|
| Read incoming emails and chats | Website chat widget, Telegram bot, email (Brevo inbound), WhatsApp Cloud API, voice |
| Classify intent and urgency | Triage model returns intent, urgency (P1–P4), sentiment, language, entities, and sensitive and prompt-injection flags |
| Draft replies from a knowledge base | Hybrid retrieval (pgvector and full-text search) with citations; replies are written in the customer's language |
| Escalate sensitive cases | Sensitive categories, low confidence and policy decisions route to a human with a summary and a suggested decision |
| LLM with tool calling | Resolver agent with read tools (orders, products, stock) and action tools (replacement, refund, cancel, address change, coupon) |
| Ticket dashboard | Priority inbox, ticket view with the full reasoning trace, approvals queue, customer timelines |
| Sentiment tracking | Per-message sentiment, a frustration score per conversation, and an at-risk customer list |
| Weekly insights report | Complaint clustering with root causes by product, courier and pincode; generated every Monday or on demand |

### Beyond the brief

- **Policy gate.** The model proposes actions; deterministic code approves, escalates or denies them using server-computed facts, platform invariants and the owner's rules (written in plain language and compiled to a strict policy).
- **Fraud checks.** Vision compares claim photos with the ordered product; perceptual hashing detects reused or catalogue images.
- **Owner approvals** from the dashboard or by signed one-tap messages.
- **Sales.** Lead capture in chat, web lead search with email verification and scoring, approved outreach with personal chat links, campaigns, cart reminders and follow-ups.
- **Agent Studio.** One agent profile per business (persona, skills, custom tools, widget and voice settings) shared by every channel.
- **Knowledge gap detection** and a **learning loop** that turns team edits into lessons and autonomy suggestions.
- **Simulations.** AI test customers produce a readiness score; shadow mode replays past tickets and compares outcomes.
- **Integrations.** Shopify, WooCommerce or any site through a documented API, events, signed webhooks and SDKs (Node, Python, PHP, widget).
- **Multi-tenant** with Postgres row-level security on every tenant table.

## Architecture

```
Channels (widget · Telegram · email · WhatsApp · voice)
        │  tenant resolution · dedupe · PII redaction
        ▼
  triage ─► ticket + frustration score
        │
        ▼
  agent profile ─► skills ─► allowed tools
        │
        ▼
  resolver (LLM + tenant knowledge + live store data)
        │ action proposals
        ▼
  policy gate (code) ─► execute │ owner approval │ deny
        │
        ▼
  composer ─► reply guard ─► send or draft
```

Background work runs on separate lanes (support, sales, lifecycle, knowledge) so long jobs never delay customer replies.

**Stack:** FastAPI (Python 3.12), Postgres 17 with pgvector, Gemini on Vertex AI, React with Vite and Tailwind. Deployed on Cloud Run with Cloud SQL, Cloud Storage and Secret Manager. Model and thinking-level settings are in `apps/api/app/config.py`.

## Repository

```
apps/api       FastAPI service: pipeline, policy gate, tools, channels, insights
apps/web       Dashboard and public pages (React)
apps/widget    Embeddable chat widget
packages/      SDKs: widget, node, python, php
integrations/  Shopify app embed block, WordPress plugin
seed/          Demo businesses: products, orders, knowledge base
infra/         Deployment scripts and Cloud Run configuration
scripts/       End-to-end checks and operational helpers
docs/          Implementation plan
```

## Run locally

Requires Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, Docker, and `gcloud auth application-default login` (Vertex AI and Cloud Storage).

```bash
cp .env.example .env                 # set passwords, GCP project and bucket
docker compose up -d                 # Postgres with pgvector on :5433
cd apps/api && uv sync
uv run python -m app.db.migrate      # schema, row-level security, runtime role
uv run python -m app.seed --reset    # demo businesses
uv run uvicorn app.main:app --port 8080
cd ../web && npm install && npm run build
```

The app is served at http://localhost:8080. For frontend development, run `npm run dev` in `apps/web` (port 5173, proxies the API).

## Configuration

All settings are environment variables; see `.env.example`. Core settings (database, GCP, JWT) are required. Channels are optional and enabled by adding their credentials, either in `.env` or per business under Integrations:

| Channel | Variables |
|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` |
| Email (Brevo) | `BREVO_API_KEY`, `EMAIL_FROM`, `INBOUND_EMAIL_DOMAIN` |
| Voice (LiveKit, Sarvam) | `LIVEKIT_*`, `SARVAM_API_KEY` |
| WhatsApp Cloud API | `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` |
| Payments (Cashfree) | `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY` |

## Deployment

```bash
infra/bootstrap.sh          # one-time: APIs, Cloud SQL, bucket, registry, secrets
infra/deploy.sh --setup     # build, migrate, seed, deploy
infra/deploy.sh --migrate   # apply schema changes, keep data
infra/deploy.sh             # build and deploy
```

The service runs as a single always-on Cloud Run instance with a small Cloud SQL database and one hourly Cloud Scheduler job that drives the lifecycle worker (alerts, follow-ups, daily summary, weekly report, newsletter). Redeploying restarts in-process background jobs, so avoid deploying while a simulation is running.

## Testing

End-to-end checks run against a running API:

```bash
cd apps/api
uv run python ../../scripts/e2e_sales.py http://localhost:8080 loop-loot
uv run python ../../scripts/e2e_connect.py http://localhost:8080
```

## Developer documentation

API reference and the Commerce Contract (OpenAPI) are served at `/docs`. The full design is in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Known limitations

- Background jobs run in-process on a single instance; there is no external queue.
- Authentication is email and password with JWT.
- Business integration secrets are stored in the database without KMS envelope encryption.
- Rate limits are held in memory.
- WhatsApp requires Meta business verification; Telegram is the live customer messaging channel until then.
- Payments run against the Cashfree sandbox.

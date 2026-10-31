-- Sahay schema. Applied by `python -m app.db.migrate` as the DB owner.
-- Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Global tables (no RLS, no customer data)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  name text NOT NULL,
  password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_members (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role text NOT NULL DEFAULT 'owner',
  phone_e164 text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS channel_routes (
  channel text NOT NULL,
  external_key text NOT NULL,
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (channel, external_key)
);

-- ---------------------------------------------------------------------------
-- Tenant tables (RLS enforced below)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_settings (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  brand_voice text NOT NULL DEFAULT '',
  business_description text NOT NULL DEFAULT '',
  website_url text,
  languages text[] NOT NULL DEFAULT ARRAY['en','hinglish'],
  default_autonomy text NOT NULL DEFAULT 'draft',
  autonomy jsonb NOT NULL DEFAULT '{}'::jsonb,
  owner_phone text,
  owner_wa_approvals boolean NOT NULL DEFAULT true,
  debounce_ms int NOT NULL DEFAULT 3000,
  sla_minutes int NOT NULL DEFAULT 60,
  intents text[] NOT NULL DEFAULT ARRAY[]::text[],
  brand_color text NOT NULL DEFAULT '#4f46e5',
  widget_greeting text NOT NULL DEFAULT 'Hi! How can we help you today?',
  daily_llm_budget_inr int NOT NULL DEFAULT 300,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kind)
);

-- Mock commerce store (used by the "mock" commerce adapter)
CREATE TABLE IF NOT EXISTS commerce_products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  sku text NOT NULL,
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  category text NOT NULL DEFAULT 'general',
  price_paise bigint NOT NULL,
  stock int NOT NULL DEFAULT 0,
  image_uri text,
  image_phash text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (tenant_id, sku)
);

CREATE TABLE IF NOT EXISTS commerce_orders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_ref text NOT NULL,
  customer_name text NOT NULL,
  customer_phone text,
  customer_email text,
  line_items jsonb NOT NULL,
  total_paise bigint NOT NULL,
  payment_method text NOT NULL,
  status text NOT NULL,
  shipping_address jsonb NOT NULL DEFAULT '{}'::jsonb,
  courier text,
  awb text,
  shipment_status text,
  expected_delivery date,
  delivered_at timestamptz,
  tags text[] NOT NULL DEFAULT ARRAY[]::text[],
  refunded_paise bigint NOT NULL DEFAULT 0,
  placed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, order_ref)
);

CREATE TABLE IF NOT EXISTS commerce_discounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  code text NOT NULL,
  value_paise bigint NOT NULL,
  customer_ref text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  display_name text,
  primary_phone text,
  primary_email text,
  language_pref text,
  vip boolean NOT NULL DEFAULT false,
  fraud_score real NOT NULL DEFAULT 0,
  frustration_score real NOT NULL DEFAULT 0,
  is_simulation boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_identities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  kind text NOT NULL,
  value text NOT NULL,
  verified boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, kind, value)
);

CREATE TABLE IF NOT EXISTS conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  channel text NOT NULL,
  external_thread_id text,
  is_simulation boolean NOT NULL DEFAULT false,
  last_inbound_at timestamptz,
  last_outbound_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_customer_idx ON conversations (tenant_id, customer_id);

CREATE TABLE IF NOT EXISTS tickets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  number serial,
  customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  subject text,
  status text NOT NULL DEFAULT 'open',
  intent text,
  urgency text,
  sentiment real,
  frustration_score real NOT NULL DEFAULT 0,
  priority_score real NOT NULL DEFAULT 0,
  sensitive boolean NOT NULL DEFAULT false,
  sensitive_categories text[] NOT NULL DEFAULT ARRAY[]::text[],
  injection_suspected boolean NOT NULL DEFAULT false,
  language text,
  order_refs text[] NOT NULL DEFAULT ARRAY[]::text[],
  resolution_type text,
  summary text,
  complaint_summary text,
  complaint_embedding vector(768),
  entities jsonb NOT NULL DEFAULT '{}'::jsonb,
  csat int,
  is_simulation boolean NOT NULL DEFAULT false,
  first_response_at timestamptz,
  resolved_at timestamptz,
  sla_due_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS assignee text;
CREATE INDEX IF NOT EXISTS tickets_tenant_status_idx ON tickets (tenant_id, status, priority_score DESC);

CREATE TABLE IF NOT EXISTS messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(id) ON DELETE SET NULL,
  customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  direction text NOT NULL,
  channel text NOT NULL,
  author_type text NOT NULL,
  author_name text,
  text text,
  text_redacted text,
  transcript text,
  language text,
  sentiment real,
  triage jsonb,
  interactive jsonb,
  external_id text,
  status text NOT NULL DEFAULT 'received',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_external_uidx ON messages (tenant_id, channel, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS messages_conv_idx ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS attachments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  message_id uuid REFERENCES messages(id) ON DELETE CASCADE,
  kind text NOT NULL,
  mime text NOT NULL,
  uri text NOT NULL,
  sha256 text,
  phash text,
  analysis jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(id) ON DELETE CASCADE,
  trigger_message_id uuid,
  kind text NOT NULL DEFAULT 'resolve',
  status text NOT NULL DEFAULT 'running',
  plan jsonb,
  error text,
  latency_ms int,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  seq int NOT NULL,
  type text NOT NULL,
  name text,
  input jsonb,
  output jsonb,
  latency_ms int,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  purpose text NOT NULL,
  model text NOT NULL,
  input_tokens int,
  output_tokens int,
  cost_micros bigint,
  latency_ms int,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drafts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ticket_id uuid NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  channel text NOT NULL,
  text text NOT NULL,
  claims jsonb NOT NULL DEFAULT '[]'::jsonb,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  guard jsonb NOT NULL DEFAULT '{}'::jsonb,
  intent text,
  status text NOT NULL DEFAULT 'draft',
  created_by text NOT NULL DEFAULT 'ai',
  sent_message_id uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS human_edits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  draft_id uuid REFERENCES drafts(id) ON DELETE CASCADE,
  intent text,
  original_text text NOT NULL,
  final_text text NOT NULL,
  edit_ratio real NOT NULL,
  lesson_extracted boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS playbook_lessons (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  intent text,
  lesson text NOT NULL,
  status text NOT NULL DEFAULT 'suggested',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  source text NOT NULL,
  title text NOT NULL,
  url text,
  content_md text NOT NULL,
  checksum text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  document_id uuid NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
  ord int NOT NULL,
  heading_path text,
  content text NOT NULL,
  embedding vector(768),
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(heading_path,'') || ' ' || content)) STORED
);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx ON kb_chunks USING gin (tsv);

CREATE TABLE IF NOT EXISTS kb_gaps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  question text NOT NULL,
  embedding vector(768),
  ticket_id uuid REFERENCES tickets(id) ON DELETE SET NULL,
  suggestion_id uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_gap_suggestions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  label text NOT NULL,
  questions jsonb NOT NULL,
  draft_title text NOT NULL,
  draft_article_md text NOT NULL,
  status text NOT NULL DEFAULT 'suggested',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  version int NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  source_text text NOT NULL,
  rules jsonb NOT NULL,
  questions jsonb NOT NULL DEFAULT '[]'::jsonb,
  confirmed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, version)
);

CREATE TABLE IF NOT EXISTS actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(id) ON DELETE CASCADE,
  customer_id uuid REFERENCES customers(id) ON DELETE CASCADE,
  type text NOT NULL,
  params jsonb NOT NULL,
  facts jsonb NOT NULL DEFAULT '{}'::jsonb,
  decision text,
  decision_reason text,
  matched_rules text[] NOT NULL DEFAULT ARRAY[]::text[],
  status text NOT NULL DEFAULT 'proposed',
  proposed_by text NOT NULL,
  idempotency_key text NOT NULL,
  dry_run boolean NOT NULL DEFAULT false,
  value_paise bigint NOT NULL DEFAULT 0,
  result jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(id) ON DELETE CASCADE,
  action_id uuid REFERENCES actions(id) ON DELETE CASCADE,
  draft_id uuid REFERENCES drafts(id) ON DELETE CASCADE,
  kind text NOT NULL,
  summary text,
  suggestion text,
  status text NOT NULL DEFAULT 'pending',
  decided_by text,
  decided_via text,
  decided_at timestamptz,
  expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  actor_type text NOT NULL,
  actor text,
  verb text NOT NULL,
  object_type text,
  object_id text,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS security_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(id) ON DELETE CASCADE,
  kind text NOT NULL,
  severity text NOT NULL DEFAULT 'medium',
  detail text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fraud_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id uuid REFERENCES customers(id) ON DELETE CASCADE,
  ticket_id uuid REFERENCES tickets(id) ON DELETE CASCADE,
  attachment_id uuid REFERENCES attachments(id) ON DELETE CASCADE,
  kind text NOT NULL,
  score real NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS insights_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  period_start date NOT NULL,
  period_end date NOT NULL,
  stats jsonb NOT NULL,
  clusters jsonb NOT NULL,
  narrative_md text NOT NULL,
  recommendations jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sim_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind text NOT NULL,
  status text NOT NULL DEFAULT 'running',
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  summary jsonb,
  readiness_score real,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sim_cases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES sim_runs(id) ON DELETE CASCADE,
  persona jsonb NOT NULL,
  ticket_id uuid,
  transcript jsonb NOT NULL DEFAULT '[]'::jsonb,
  actions jsonb NOT NULL DEFAULT '[]'::jsonb,
  judge jsonb,
  safety_failures jsonb NOT NULL DEFAULT '[]'::jsonb,
  passed boolean,
  status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Agent Profile: one per business, shared by widget, email chat, WhatsApp and voice
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_profiles (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  agent_name text NOT NULL DEFAULT 'Sahay',
  persona text NOT NULL DEFAULT '',
  skills jsonb NOT NULL DEFAULT '{}'::jsonb,
  custom_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
  sales jsonb NOT NULL DEFAULT '{}'::jsonb,
  voice jsonb NOT NULL DEFAULT '{}'::jsonb,
  widget jsonb NOT NULL DEFAULT '{}'::jsonb,
  secret_key text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE messages ADD COLUMN IF NOT EXISTS context jsonb;

-- ---------------------------------------------------------------------------
-- Sales: leads, outreach, campaigns
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lead_searches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  query text NOT NULL,
  target_count int NOT NULL DEFAULT 10,
  status text NOT NULL DEFAULT 'running',
  found int NOT NULL DEFAULT 0,
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id uuid REFERENCES customers(id) ON DELETE SET NULL,
  search_id uuid REFERENCES lead_searches(id) ON DELETE SET NULL,
  name text,
  company text,
  email text,
  phone text,
  website text,
  city text,
  source text NOT NULL,
  stage text NOT NULL DEFAULT 'new',
  score int,
  score_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  interest text,
  notes text,
  email_status text,
  enrichment jsonb NOT NULL DEFAULT '{}'::jsonb,
  next_action text,
  next_action_at timestamptz,
  last_contact_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS leads_stage_idx ON leads (tenant_id, stage, score DESC);
CREATE INDEX IF NOT EXISTS leads_customer_idx ON leads (tenant_id, customer_id);

CREATE TABLE IF NOT EXISTS lead_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  kind text NOT NULL,
  summary text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outreach_emails (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'first',
  subject text NOT NULL,
  body text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  external_id text,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  goal text NOT NULL,
  channel text NOT NULL DEFAULT 'whatsapp',
  audience jsonb NOT NULL DEFAULT '{}'::jsonb,
  message text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'draft',
  stats jsonb NOT NULL DEFAULT '{}'::jsonb,
  approved_by text,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campaign_sends (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  customer_id uuid REFERENCES customers(id) ON DELETE CASCADE,
  status text NOT NULL,
  skip_reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Voice calls + proactive (lifecycle) messages
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS voice_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  direction text NOT NULL,
  purpose text NOT NULL DEFAULT 'inbound',
  from_number text,
  to_number text,
  customer_id uuid REFERENCES customers(id) ON DELETE SET NULL,
  lead_id uuid REFERENCES leads(id) ON DELETE SET NULL,
  ticket_id uuid REFERENCES tickets(id) ON DELETE SET NULL,
  conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'queued',
  transcript jsonb NOT NULL DEFAULT '[]'::jsonb,
  summary text,
  outcome text,
  next_step text,
  context jsonb NOT NULL DEFAULT '{}'::jsonb,
  scheduled_for timestamptz,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind text NOT NULL,
  ref text NOT NULL,
  customer_id uuid REFERENCES customers(id) ON DELETE SET NULL,
  summary text NOT NULL DEFAULT '',
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kind, ref)
);

-- Abandoned carts (widget/storefront checkouts, or synced from Shopify abandoned checkouts)
CREATE TABLE IF NOT EXISTS commerce_carts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  cart_ref text NOT NULL,
  customer_name text,
  customer_phone text,
  customer_email text,
  items jsonb NOT NULL DEFAULT '[]'::jsonb,
  total_paise bigint NOT NULL DEFAULT 0,
  checkout_url text,
  source text NOT NULL DEFAULT 'widget',
  status text NOT NULL DEFAULT 'open',
  reminded_at timestamptz,
  converted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, cart_ref)
);

ALTER TABLE customers ADD COLUMN IF NOT EXISTS marketing_opt_out boolean NOT NULL DEFAULT false;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'campaign';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS subject text;

-- Per-business ticket numbers (LL-1042) and the business's own integrations
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS ticket_prefix text;
ALTER TABLE commerce_orders ADD COLUMN IF NOT EXISTS tracking_url text;
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS ticket_seq int NOT NULL DEFAULT 1000;

-- Events their website sends us (order shipped, product updated, ...). The id makes delivery idempotent.
CREATE TABLE IF NOT EXISTS integration_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  external_id text NOT NULL,
  type text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'processed',
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, external_id)
);

-- Webhooks we send to the business (their Slack, CRM, ...), signed and retried
CREATE TABLE IF NOT EXISTS webhook_endpoints (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  url text NOT NULL,
  events text[] NOT NULL DEFAULT ARRAY['*'],
  secret text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  endpoint_id uuid NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
  event text NOT NULL,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempts int NOT NULL DEFAULT 0,
  last_status_code int,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz
);

ALTER TABLE IF EXISTS telegram_chats ADD COLUMN IF NOT EXISTS pending_name text;
-- authenticity screening of every image/PDF a customer sends (see app/authenticity.py)
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS screening jsonb;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS filename text;

-- Telegram chats (global routing, like channel_routes): which business + customer a chat proved it belongs to
CREATE TABLE IF NOT EXISTS telegram_chats (
  chat_id text PRIMARY KEY,
  tenant_id uuid REFERENCES tenants(id) ON DELETE SET NULL,
  customer_id uuid,
  order_ref text,
  verified boolean NOT NULL DEFAULT false,
  tries int NOT NULL DEFAULT 0,
  pending_order text,
  pending_name text,
  preferred_tenant uuid REFERENCES tenants(id) ON DELETE SET NULL,
  tg_name text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Runtime role + row-level security
-- ---------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT unnest(ARRAY[
    'tenant_settings','integrations','commerce_products','commerce_orders','commerce_discounts',
    'customers','customer_identities','conversations','tickets','messages','attachments',
    'agent_runs','agent_steps','llm_calls','drafts','human_edits','playbook_lessons',
    'kb_documents','kb_chunks','kb_gaps','kb_gap_suggestions','policies','actions','approvals',
    'audit_log','security_events','fraud_signals','insights_reports','sim_runs','sim_cases',
    'agent_profiles','lead_searches','leads','lead_events','outreach_emails','campaigns','campaign_sends',
    'voice_calls','lifecycle_events','commerce_carts','integration_events','webhook_endpoints','webhook_deliveries'])
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
      USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
      WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)$p$, t);
  END LOOP;
END $$;

UPDATE tenant_settings s SET ticket_seq = GREATEST(s.ticket_seq, (SELECT coalesce(max(t.number), 1000) FROM tickets t WHERE t.tenant_id = s.tenant_id));

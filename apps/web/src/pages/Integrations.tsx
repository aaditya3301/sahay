import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ExternalLink, Eye, MessageCircle, PackageCheck, RefreshCw, Send, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import SettingsPage, { useBusinessSettings } from './Settings'
import { PersonaTab, useStudioProfile } from './Studio'
import { Badge, Button, Card, CodeBlock, cx, Empty, Field, inputCls, PageHeader, Spinner, Tabs } from '../components/ui'
import { api } from '../lib/api'
import { ago, humanize } from '../lib/format'

const copy = (t: string) => navigator.clipboard.writeText(t)
const dot = (s: string) => ({ green: 'bg-emerald-500', amber: 'bg-amber-400', red: 'bg-red-500' } as any)[s] || 'bg-slate-300'
const statusTone = (s: string) => ({ processed: 'green', delivered: 'green', ignored: 'slate', failed: 'red', retrying: 'amber', pending: 'amber' } as any)[s] || 'slate'

export default function Integrations() {
  const [params, setParams] = useSearchParams()
  const tabs = ['store', 'agent', 'business', 'status']
  const asked = params.get('tab') === 'voice' ? 'agent' : params.get('tab') || ''  // old ?tab=voice links land on Voice agent
  const tab = tabs.includes(asked) ? asked : 'store'
  const setTab = (next: string) => setParams((previous) => { const q = new URLSearchParams(previous); q.set('tab', next); return q }, { replace: true })
  const studio = useStudioProfile()
  const business = useBusinessSettings()
  const store = useStoreConnection()
  const profileTab = tab === 'agent'
  const businessTab = tab === 'business'
  const storeTab = tab === 'store'
  return (
    <>
      <div className="studio-sticky-header">
        <PageHeader title="Integrations" subtitle="Your store, channels and agent: everything you set up lives here."
          actions={profileTab ? <>{studio.save.isSuccess && !studio.dirty && <span className="text-xs text-emerald-700">Saved</span>}{studio.save.isError && <span className="text-xs text-red-600">{(studio.save.error as Error).message}</span>}
            <Button onClick={() => studio.saveAll()} loading={studio.save.isPending} disabled={!studio.ready}>Save changes</Button></>
            : businessTab ? <>{business.save.isSuccess && !business.save.isPending && <span className="text-xs text-emerald-700">Saved</span>}{business.save.isError && <span className="text-xs text-red-600">{(business.save.error as Error).message}</span>}
            <Button onClick={() => business.save.mutate()} loading={business.save.isPending} disabled={!business.ready}>Save changes</Button></>
            : storeTab ? <>{store.save.isSuccess && !store.save.isPending && <span className="text-xs text-emerald-700">Saved</span>}{store.save.isError && <span role="alert" className="max-w-[320px] text-right text-xs text-red-600">{(store.save.error as Error).message}</span>}
            <Button onClick={() => store.save.mutate()} loading={store.save.isPending} disabled={!store.data}>{store.save.isPending && store.tab === 'shopify' ? 'Checking with Shopify…' : 'Save changes'}</Button></> : null} />
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'store', label: 'Store' }, { id: 'agent', label: 'Voice agent' }, { id: 'business', label: 'Business settings' }, { id: 'status', label: 'Connection status' },
        ]} />
      </div>
      <div className="mt-4">
        {tab === 'store' && <div className="integrations-connect-layout"><StoreConnect form={store} /></div>}
        {tab === 'agent' && (studio.ready ? <div className="space-y-4">
          <PersonaTab p={studio.p} setP={studio.setP} setIn={studio.setIn} tenant={studio.data.tenant} />
        </div> : <Spinner />)}
        {tab === 'business' && <SettingsPage embedded form={business} />}
        {tab === 'status' && <Health onManage={(to) => setTab(to)} />}
      </div>
    </>
  )
}

export default function Integrations() {
  const location = useLocation()
  const navigate = useNavigate()
  const [view, setView] = useState(location.pathname === '/developer' ? 'developer' : 'connect')
  const isDeveloperPage = location.pathname === '/developer'
  const activeView = isDeveloperPage ? 'developer' : view
  const { data } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const changeView = (next: string) => {
    if (next === 'developer') navigate('/developer')
    else {
      setView(next)
      if (isDeveloperPage) navigate('/integrations')
    }
  }
  return (
    <>
      <PageHeader title={isDeveloperPage ? 'Developer docs' : 'Integrations'} subtitle={isDeveloperPage ? 'A clear setup guide for your website, widget, and developer.' : 'Start with your store, then choose how customers reach you.'} />
      <Tabs value={activeView} onChange={changeView} tabs={[{ id: 'connect', label: 'Set up connections' }, { id: 'health', label: 'Connection status' }, { id: 'developer', label: 'Developer docs' }]} />
      <section hidden={activeView !== 'connect'} className="setup-tab-content" aria-label="Set up connections">
        <div className="integrations-connect-layout">
          <Store />
          {data ? <ChannelConnections integrations={data.integrations} showOtherChannels platformEmail={data.platform_email} /> : <Spinner />}
        </div>
      </section>
      <section hidden={activeView !== 'health'} className="setup-tab-content" aria-label="Connection status"><Health onConnect={() => setView('connect')} /></section>
      <section hidden={activeView !== 'developer'} className="setup-tab-content" aria-label="Developer tools">
        <p className="setup-intro">Choose what you are connecting. You can share this page with your developer; regular store connections are under Integrations.</p>
        <div className="developer-start-grid">
          <Link className="developer-start-card" to="/studio?tab=widget"><span className="developer-start-icon"><MessageCircle size={16} /></span><span><strong>Website chat widget</strong><small>Add Sahay chat to your website. No server work needed.</small></span><span aria-hidden="true">→</span></Link>
          <details className="developer-start-card developer-start-details"><summary><span className="developer-start-icon"><PackageCheck size={16} /></span><span><strong>Store updates</strong><small>Send order and product changes from your website.</small></span><span aria-hidden="true">⌄</span></summary><div className="developer-start-links"><a href="/docs#levels" target="_blank" rel="noreferrer">Read setup guide <ExternalLink size={14} /></a><a href="/api-docs" target="_blank" rel="noreferrer">API reference <ExternalLink size={14} /></a></div></details>
          <details className="developer-start-card developer-start-details"><summary><span className="developer-start-icon"><Send size={16} /></span><span><strong>Send events to your systems</strong><small>Forward ticket and lead updates to a CRM or backend.</small></span><span aria-hidden="true">⌄</span></summary><div className="developer-start-links"><a href="/docs" target="_blank" rel="noreferrer">Webhook guide <ExternalLink size={14} /></a><a href="/api-docs" target="_blank" rel="noreferrer">API reference <ExternalLink size={14} /></a></div></details>
        </div>
        <div className="setup-developer-sections">
          <details className="connection-disclosure"><summary>Keys for your developer</summary><div><Keys /></div></details>
          <details className="connection-disclosure"><summary>Order and product updates</summary><div><Events /></div></details>
          <details className="connection-disclosure"><summary>Webhooks to your systems</summary><div><Webhooks /></div></details>
        </div>
      </section>
    </>
  )
}

// Website chat, email, Telegram and phone calls are set up centrally by Sahay, so they always read as ready here.
const CENTRAL: Record<string, { label: string; badge: string; detail: string }> = {
  widget: { label: 'Website chat', badge: 'Ready', detail: 'The website chat is ready to add to your site.' },
  telegram: { label: 'Telegram bot', badge: 'Agent bot configured', detail: 'Customers can message your agent on Telegram.' },
  email: { label: 'Email', badge: 'Ready', detail: 'Sahay sends and receives your email for you.' },
  voice: { label: 'Phone calls', badge: 'Ready', detail: 'Phone calls are set up and managed by Sahay.' },
}
const HEALTH_ROWS = ['widget', 'store', 'telegram', 'email', 'voice']

function Health({ onManage }: { onManage: (tab: string) => void }) {
  const { data, isLoading, refetch, isFetching } = useQuery({ queryKey: ['integration-health'], queryFn: () => api('/v1/integrations/health') })
  const rows = HEALTH_ROWS.map((key) => (data?.checks || []).find((c: any) => c.key === key) || (CENTRAL[key] ? { key, status: 'green', detail: '' } : null)).filter(Boolean)
  return (
    <Card title="Your connections" action={<Button size="sm" variant="secondary" onClick={() => refetch()} loading={isFetching}><RefreshCw className="h-3.5 w-3.5" />Refresh</Button>}>
      {isLoading ? <Spinner /> : (
        <div className="connection-health-list">
          {rows.map((c: any) => {
            const central = CENTRAL[c.key]
            const status = central ? 'green' : c.status
            return (
              <div key={c.key} className="connection-health-row">
                <span className={cx('connection-health-dot', dot(status))} aria-hidden="true" />
                <div><strong>{central?.label || 'Your store'}</strong>
                  {c.key !== 'voice' && <details><summary>Connection details</summary><p>{central?.detail || c.detail}</p></details>}
                </div>
                <Badge tone={status === 'red' ? 'red' : status === 'green' ? 'green' : 'slate'}>{central ? central.badge : status === 'red' ? 'Needs attention' : status === 'green' ? (c.detail.includes('demo store') ? 'Demo mode' : 'Ready') : 'Not connected'}</Badge>
                {['store', 'voice'].includes(c.key) && <Button size="sm" variant="ghost" onClick={() => onManage(c.key === 'store' ? 'store' : 'agent')}>Manage</Button>}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

const PROVIDERS = [
  { id: 'mock', label: 'Demo store' },
  { id: 'shopify', label: 'Shopify' },
  { id: 'woocommerce', label: 'WooCommerce' },
  { id: 'custom', label: 'Custom website' },
]

/** Store connection form state. The Integrations page header owns the Save button; the setup wizard uses StoreConnect on its own. */
export function useStoreConnection({ hideDemo = false, onSaved }: { hideDemo?: boolean; onSaved?: () => void } = {}) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const current = (data?.integrations || []).find((i: any) => i.kind === 'commerce')?.config || {}
  const active = current.provider || 'mock'
  const providers = hideDemo ? PROVIDERS.filter((p) => p.id !== 'mock') : PROVIDERS
  const [tab, setTab] = useState<string>('')
  const [f, setF] = useState<Record<string, string>>({})
  const [method, setMethod] = useState<'token' | 'app'>('token')  // Shopify: Admin API token, or a Dev Dashboard app's Client ID + secret
  useEffect(() => { if (data) { if (current.client_id && !current.access_token) setMethod('app'); setTab((t) => t || (hideDemo && active === 'mock' ? 'shopify' : active)); setF((x) => ({ ...current, ...x })) } }, [data])
  const save = useMutation({
    mutationFn: () => {
      const keys = ({ mock: [], shopify: ['shop_domain', ...(method === 'app' ? ['client_id', 'client_secret'] : ['access_token'])], woocommerce: ['site_url', 'consumer_key', 'consumer_secret'], custom: ['endpoint_url'] } as any)[tab] as string[]
      // blank or masked fields keep the saved value
      return api('/v1/integrations/commerce', { method: 'PUT', body: { provider: tab, ...Object.fromEntries(keys.filter((k) => f[k] && f[k] !== '••••').map((k) => [k, f[k]])) } })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['integration-health'] }); onSaved?.() },
  })
  const input = (k: string, placeholder: string, secret = false) => (
    <input name={`store-${k}`} autoComplete={secret ? 'new-password' : 'off'} className={inputCls} type={secret ? 'password' : 'text'} placeholder={placeholder} value={f[k] === '••••' ? '' : f[k] || ''}
      onChange={(e) => setF({ ...f, [k]: e.target.value })} />
  )
  return { data, current, active, providers, tab, setTab, method, setMethod, save, input }
}

type StoreForm = ReturnType<typeof useStoreConnection>

export function StoreConnect({ hideDemo = false, onSaved, form }: { hideDemo?: boolean; compact?: boolean; onSaved?: () => void; form?: StoreForm }) {
  const own = useStoreConnection({ hideDemo, onSaved })
  const { data, current, active, providers, tab, setTab, method, setMethod, save, input } = form || own
  return (
    <Card title="Connect your store" action={active === 'mock' ? undefined : <Badge tone="green">{active === 'shopify' && current.shop_name ? `Connected to ${current.shop_name}` : `Using ${PROVIDERS.find((p) => p.id === active)?.label}`}</Badge>}>
      {!data ? <Spinner /> : <>
      <p className="setup-intro">Let Sahay look up your products and customer orders.</p>
      <Field label="Where is your store?"><select className={inputCls} value={tab || active} onChange={(e) => setTab(e.target.value)}>{providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
      <div className="mt-3 space-y-3 text-sm">
        {tab === 'mock' && <p className="text-slate-600">Explore Sahay without connecting a live store. Choose your store platform above when you’re ready.</p>}
        {tab === 'shopify' && (
          <>
            <Field label="Your store's website" hint="Use the address your customers visit, like beatband.in. Sahay finds your Shopify address by itself. A yourstore.myshopify.com address works too.">{input('shop_domain', 'yourstore.in')}</Field>
            <Field label="How will you give Sahay access?">
              <div className="flex flex-wrap gap-2">
                {([['token', 'Admin API access token'], ['app', 'Client ID and secret (new Shopify apps)']] as const).map(([id, label]) => (
                  <button key={id} type="button" onClick={() => setMethod(id)} aria-pressed={method === id}
                    className={cx('rounded-lg border px-3 py-1.5 text-sm', method === id ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50')}>{label}</button>))}
              </div>
            </Field>
            {method === 'token' ? (
              <>
                <details className="dashboard-disclosure"><summary>Where do I find the access token?</summary><div className="space-y-2">
                  <ol className="connection-steps">
                    <li>In your Shopify admin, open <b>Settings → Apps and sales channels → Develop apps</b> and open your app (or create one).</li>
                    <li>Under <b>Configuration → Admin API integration</b>, tick the permissions listed below, then save and <b>Install app</b>.</li>
                    <li>Open <b>API credentials</b> and click <b>Reveal token once</b>. Copy the token that starts with <code>shpat_</code>. Shopify shows it only once.</li>
                  </ol>
                  <p className="setup-help">Shopify stopped letting new stores create these apps in 2026. If you don’t see “Develop apps” or “Create an app”, choose “Client ID and secret” above instead.</p>
                </div></details>
                <Field label="Admin API access token" hint={current.access_token ? 'A token is saved. Leave blank to keep it.' : 'Starts with shpat_'}>{input('access_token', current.access_token ? 'Saved token, leave blank to keep' : 'shpat_…', true)}</Field>
              </>
            ) : (
              <>
                <details className="dashboard-disclosure"><summary>Where do I find the Client ID and secret?</summary><div className="space-y-2">
                  <ol className="connection-steps">
                    <li>Sign in at <a className="setup-text-link" href="https://dev.shopify.com/dashboard" target="_blank" rel="noreferrer">dev.shopify.com/dashboard</a> with the Shopify account that owns your store.</li>
                    <li>Click <b>Create app</b>, then under <b>Versions</b> add the permissions listed below and <b>Release</b>.</li>
                    <li>Click <b>Install app</b> and choose your store.</li>
                    <li>Open <b>Settings</b> in the app and copy the <b>Client ID</b> and <b>Client secret</b>.</li>
                  </ol>
                  <p className="setup-help">The app and the store must be under the same Shopify account (organization). Sahay renews the access automatically every day.</p>
                </div></details>
                <div className="setup-fields">
                  <Field label="Client ID">{input('client_id', 'Paste the Client ID')}</Field>
                  <Field label="Client secret" hint={current.client_secret ? 'A secret is saved. Leave blank to keep it.' : undefined}>{input('client_secret', current.client_secret ? 'Saved secret, leave blank to keep' : 'Paste the Client secret', true)}</Field>
                </div>
              </>
            )}
            <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
              <div className="mb-1 font-medium text-slate-700">Permissions to give the app (read only)</div>
              <div className="flex flex-wrap gap-1.5">{['read_orders', 'read_products', 'read_customers', 'read_inventory'].map((x) => <code key={x} className="rounded bg-white px-1.5 py-0.5">{x}</code>)}</div>
              <p className="mt-1.5">Optional: <code>read_all_orders</code> lets Sahay find orders older than 60 days. Sahay only reads your store. It never changes orders in Shopify.</p>
            </div>
          </>
        )}
        {tab === 'woocommerce' && (
          <>
            <ol className="connection-steps"><li>In WooCommerce, open Settings → Advanced → REST API.</li><li>Create a key with read/write access, then enter the details below.</li></ol>
            <div className="setup-fields"><Field label="Store website address">{input('site_url', 'https://yourstore.in')}</Field><Field label="Consumer key">{input('consumer_key', current.consumer_key ? 'Saved key — leave blank to keep' : 'Starts with ck_', true)}</Field><Field label="Consumer secret">{input('consumer_secret', current.consumer_secret ? 'Saved secret — leave blank to keep' : 'Starts with cs_', true)}</Field></div>
          </>
        )}
        {tab === 'custom' && (
          <>
            <p className="text-slate-600">Start by adding website chat. Your developer can connect live order updates and order actions later.</p>
            <Link className="setup-text-link" to="/studio?tab=widget">Set up website chat →</Link>
            <details className="dashboard-disclosure"><summary>Advanced: connect order actions</summary><div className="space-y-3"><Field label="Order actions address (optional)" hint="Your developer supplies this address. Leave blank to start with chat and order updates.">
              {input('endpoint_url', 'https://yoursite.in/sahay')}
            </Field>
            <a href="/docs#levels" target="_blank" className="setup-text-link">Setup instructions for your developer →</a></div></details>
          </>
        )}
        {!form && (
          <div className="flex items-center gap-2">
            <Button onClick={() => save.mutate()} loading={save.isPending}>{save.isPending && tab === 'shopify' ? 'Checking with Shopify…' : tab === 'shopify' ? 'Connect store' : 'Save connection'}</Button>
            {save.isError && <span role="alert" className="text-xs text-red-600">{(save.error as Error).message}</span>}
          </div>
        )}
        {save.isSuccess && (
          <div role="status" className="rounded-lg bg-emerald-50 p-3 text-xs text-emerald-900">
            {save.data?.connected
              ? <>Connected to <b>{save.data.connected.shop_name}</b> ({save.data.connected.shop_domain}). Sahay can now look up your real products and orders.
                {(save.data.connected.warnings || []).map((w: string) => <div key={w} className="mt-1 text-amber-800">{w}</div>)}</>
              : 'Connection details saved.'}
          </div>
        )}
      </div>
      </>}
    </Card>
  )
}

function Keys() {
  const { data } = useQuery({ queryKey: ['studio'], queryFn: () => api('/v1/studio') })
  const [secret, setSecret] = useState<string | null>(null)
  const reveal = useMutation({ mutationFn: () => api('/v1/studio/secret'), onSuccess: (r) => setSecret(r.secret_key) })
  return (
    <Card title="Your keys">
      {!data ? <Spinner /> : (
        <div className="space-y-3 text-sm">
          <div>
            <div className="text-xs font-medium text-slate-500">Site key (public, used in the browser)</div>
            <div className="flex items-center gap-2"><code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{data.site_key}</code>
              <button onClick={() => copy(data.site_key)} className="text-slate-400 hover:text-slate-700"><Copy className="h-3.5 w-3.5" /></button></div>
          </div>
          <div>
            <div className="text-xs font-medium text-slate-500">Secret key (only on your server: events, API, signing, voice hooks)</div>
            <div className="flex items-center gap-2"><code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{secret || data.profile?.secret_key_hint || '—'}</code>
              {secret ? <button onClick={() => copy(secret)} className="text-slate-400 hover:text-slate-700"><Copy className="h-3.5 w-3.5" /></button>
                : <Button size="sm" variant="secondary" onClick={() => reveal.mutate()} loading={reveal.isPending}><Eye className="h-3.5 w-3.5" />Reveal</Button>}
            </div>
            {reveal.isError && <div className="text-xs text-red-600">{(reveal.error as Error).message}</div>}
          </div>
          <p className="text-xs text-slate-500">Rotate the secret key in Agent Studio → Website widget. Never put the secret key in browser code.</p>
        </div>
      )}
    </Card>
  )
}

function Events() {
  const { data, isLoading } = useQuery({ queryKey: ['integration-events'], queryFn: () => api('/v1/integration-events'), refetchInterval: 8000 })
  const example = `curl -X POST ${location.origin}/v1/api/events \\
  -H "Authorization: Bearer sk_..." -H "Content-Type: application/json" \\
  -d '{"id":"evt_1","type":"order.shipped","data":{"orderRef":"1001","courier":"Delhivery","trackingUrl":"https://..."}}'`
  return (
    <Card title="Events from your site" pad={false}>
      <div className="grid gap-4 p-4 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-2 text-xs text-slate-600">
          <p>Your server tells Sahay what happened: <code>order.placed</code>, <code>order.shipped</code>, <code>order.delivered</code>, <code>order.cancelled</code>, <code>product.updated</code>, <code>customer.updated</code>, <code>cart.updated</code>, <code>exchange.shipped</code>. Shipping updates reach the customer on Telegram and join their open ticket. Repeated event ids are ignored.</p>
          <CodeBlock title="Example request (curl)">{example}</CodeBlock>
        </div>
        <div>
          {isLoading ? <Spinner /> : !data?.length ? <Empty title="No events yet">Optional (level 2). Shopify and WooCommerce don't need this.</Empty> : (
            <div className="max-h-72 overflow-y-auto scroll-thin">
              <table className="w-full text-xs">
                <thead className="text-left text-slate-500"><tr><th className="py-1">Type</th><th>Event id</th><th>Status</th><th>Result</th><th>When</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {data.map((e: any) => (
                    <tr key={e.external_id}>
                      <td className="py-1.5 font-medium">{e.type}</td><td className="max-w-[120px] truncate text-slate-500" title={e.external_id}>{e.external_id}</td>
                      <td><Badge tone={statusTone(e.status)}>{e.status}</Badge></td>
                      <td className="max-w-[220px] truncate text-slate-500" title={JSON.stringify(e.result)}>{e.result?.error || Object.entries(e.result || {}).map(([k, v]) => `${humanize(k)}: ${v}`).join(' · ')}</td>
                      <td className="text-slate-400">{ago(e.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

function Webhooks() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['webhooks'], queryFn: () => api('/v1/webhooks'), refetchInterval: 8000 })
  const [url, setUrl] = useState('')
  const [events, setEvents] = useState<string[]>(['*'])
  const [newSecret, setNewSecret] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, any>>({})
  const refresh = () => qc.invalidateQueries({ queryKey: ['webhooks'] })
  const add = useMutation({ mutationFn: () => api('/v1/webhooks', { body: { url, events } }), onSuccess: (r) => { setNewSecret(r.secret); setUrl(''); refresh() } })
  const del = useMutation({ mutationFn: (id: string) => api(`/v1/webhooks/${id}`, { method: 'DELETE' }), onSuccess: refresh })
  const test = useMutation({ mutationFn: (id: string) => api(`/v1/webhooks/${id}/test`, { method: 'POST' }), onSuccess: (r, id) => { setTestResult((x) => ({ ...x, [id]: r })); refresh() },
    onError: (e: Error, id) => setTestResult((x) => ({ ...x, [id]: { status: 'failed', last_error: e.message } })) })
  const toggle = (ev: string) => {
    if (ev === '*') return setEvents(['*'])
    const cur = events.filter((x) => x !== '*')
    const next = cur.includes(ev) ? cur.filter((x) => x !== ev) : [...cur, ev]
    setEvents(next.length ? next : ['*'])
  }
  return (
    <Card title="Webhooks to your systems">
      {isLoading ? <Spinner /> : (
        <div className="space-y-4">
          <p className="text-xs text-slate-500">Get ticket and lead events in your Slack, CRM or backend. Each delivery is signed (<code>X-Sahay-Signature</code> = HMAC-SHA256 of <code>timestamp.body</code> with the endpoint's secret) and retried 3 times.</p>
          <div className="space-y-2 rounded-lg border border-slate-100 p-3">
            <div className="flex gap-2"><input className={inputCls} placeholder="https://hooks.slack.com/… or https://yourapp.in/sahay-webhook" value={url} onChange={(e) => setUrl(e.target.value)} />
              <Button onClick={() => add.mutate()} loading={add.isPending} disabled={!url.trim()}>Add endpoint</Button></div>
            <div className="flex flex-wrap gap-3 text-xs">
              <label className="flex items-center gap-1"><input type="checkbox" checked={events.includes('*')} onChange={() => toggle('*')} />All events</label>
              {(data?.events || []).map((ev: string) => <label key={ev} className="flex items-center gap-1"><input type="checkbox" checked={events.includes(ev)} onChange={() => toggle(ev)} />{ev}</label>)}
            </div>
            {add.isError && <div className="text-xs text-red-600">{(add.error as Error).message}</div>}
            {newSecret && (
              <div className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Signing secret (shown once, copy it now):
                <div className="mt-1 flex items-center gap-2"><code className="rounded bg-white px-1.5 py-0.5">{newSecret}</code><button onClick={() => copy(newSecret)}><Copy className="h-3.5 w-3.5" /></button>
                  <button className="ml-auto underline" onClick={() => setNewSecret(null)}>Done</button></div></div>
            )}
          </div>
          {!data?.endpoints?.length ? <div className="text-xs text-slate-500">No endpoints yet.</div> : (
            <div className="divide-y divide-slate-100 rounded-lg border border-slate-100">
              {data.endpoints.map((ep: any) => (
                <div key={ep.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                  <div className="min-w-0 flex-1"><div className="truncate font-medium">{ep.url}</div>
                    <div className="text-xs text-slate-500">{(ep.events || []).join(', ')} · secret {ep.secret_hint}</div></div>
                  {testResult[ep.id] && <Badge tone={statusTone(testResult[ep.id].status)}>{testResult[ep.id].status}{testResult[ep.id].last_status_code ? ` ${testResult[ep.id].last_status_code}` : ''}</Badge>}
                  {testResult[ep.id]?.last_error && <span className="max-w-[220px] truncate text-xs text-red-600" title={testResult[ep.id].last_error}>{testResult[ep.id].last_error}</span>}
                  <Button size="sm" variant="secondary" onClick={() => test.mutate(ep.id)} loading={test.isPending && test.variables === ep.id}><Send className="h-3.5 w-3.5" />Send test</Button>
                  <button title="Delete" onClick={() => del.mutate(ep.id)} className="text-slate-400 hover:text-red-600"><Trash2 className="h-4 w-4" /></button>
                </div>
              ))}
            </div>
          )}
          {!!data?.deliveries?.length && (
            <div>
              <div className="mb-1 text-xs font-semibold text-slate-500">Recent deliveries</div>
              <div className="max-h-64 overflow-y-auto scroll-thin">
                <table className="w-full text-xs">
                  <thead className="text-left text-slate-500"><tr><th className="py-1">Event</th><th>Endpoint</th><th>Status</th><th>Attempts</th><th>Code</th><th>Error</th><th>When</th></tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.deliveries.map((d: any) => (
                      <tr key={d.id}>
                        <td className="py-1.5 font-medium">{d.event}</td><td className="max-w-[180px] truncate text-slate-500" title={d.url}>{d.url}</td>
                        <td><Badge tone={statusTone(d.status)}>{d.status}</Badge></td><td>{d.attempts}</td><td>{d.last_status_code ?? '—'}</td>
                        <td className="max-w-[200px] truncate text-red-600" title={d.last_error || ''}>{d.last_error || ''}</td><td className="text-slate-400">{ago(d.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

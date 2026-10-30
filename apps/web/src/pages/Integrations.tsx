import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ExternalLink, Eye, RefreshCw, Send, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, cx, Empty, Field, inputCls, PageHeader, Spinner, Tabs } from '../components/ui'
import { api } from '../lib/api'
import { ago, humanize } from '../lib/format'

const copy = (t: string) => navigator.clipboard.writeText(t)
const dot = (s: string) => ({ green: 'bg-emerald-500', amber: 'bg-amber-400', red: 'bg-red-500' } as any)[s] || 'bg-slate-300'
const statusTone = (s: string) => ({ processed: 'green', delivered: 'green', ignored: 'slate', failed: 'red', retrying: 'amber', pending: 'amber' } as any)[s] || 'slate'

function Code({ children }: { children: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 pr-10 text-[11px] leading-relaxed text-slate-100 scroll-thin">{children}</pre>
      <button title="Copy" onClick={() => copy(children)} className="absolute right-2 top-2 rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"><Copy className="h-3.5 w-3.5" /></button>
    </div>
  )
}

export default function Integrations() {
  return (
    <>
      <PageHeader title="Integrations & developers" subtitle="Tickets and conversations live in Sahay. Orders, products and customers stay on your store; connectors join the two."
        actions={<div className="flex gap-2">
          <a href="/docs" target="_blank"><Button variant="secondary"><ExternalLink className="h-4 w-4" />Developer docs</Button></a>
          <a href="/api-docs" target="_blank"><Button variant="secondary">API reference</Button></a>
        </div>} />
      <div className="space-y-4">
        <Health />
        <div className="grid gap-4 lg:grid-cols-2">
          <Store />
          <Keys />
        </div>
        <Events />
        <Webhooks />
      </div>
    </>
  )
}

function Health() {
  const { data, isLoading, refetch, isFetching } = useQuery({ queryKey: ['integration-health'], queryFn: () => api('/v1/integrations/health') })
  return (
    <Card title="Integration health" action={<Button size="sm" variant="secondary" onClick={() => refetch()} loading={isFetching}><RefreshCw className="h-3.5 w-3.5" />Refresh</Button>}>
      {isLoading ? <Spinner /> : (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {(data?.checks || []).map((c: any) => (
            <div key={c.key} className="rounded-lg border border-slate-100 p-2.5">
              <div className="flex items-center gap-2 text-sm font-medium"><span className={cx('h-2.5 w-2.5 rounded-full', dot(c.status))} />{c.name}</div>
              <div className="mt-1 text-xs text-slate-500">{c.detail}</div>
            </div>
          ))}
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

function Store() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const current = (data?.integrations || []).find((i: any) => i.kind === 'commerce')?.config || {}
  const active = current.provider || 'mock'
  const [tab, setTab] = useState<string>('')
  const [f, setF] = useState<Record<string, string>>({})
  useEffect(() => { if (data) { setTab((t) => t || active); setF((x) => ({ ...current, ...x })) } }, [data])
  const save = useMutation({
    mutationFn: () => {
      const keys = ({ mock: [], shopify: ['shop_domain', 'access_token'], woocommerce: ['site_url', 'consumer_key', 'consumer_secret'], custom: ['endpoint_url'] } as any)[tab] as string[]
      // blank or masked fields keep the saved value
      return api('/v1/integrations/commerce', { method: 'PUT', body: { provider: tab, ...Object.fromEntries(keys.filter((k) => f[k] && f[k] !== '••••').map((k) => [k, f[k]])) } })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['integration-health'] }) },
  })
  const input = (k: string, placeholder: string, secret = false) => (
    <input className={inputCls} type={secret ? 'password' : 'text'} placeholder={placeholder} value={f[k] === '••••' ? '' : f[k] || ''}
      onChange={(e) => setF({ ...f, [k]: e.target.value })} />
  )
  return (
    <Card title="Connect your store" action={<Badge tone="green">active: {PROVIDERS.find((p) => p.id === active)?.label}</Badge>}>
      <Tabs value={tab || active} onChange={setTab} tabs={PROVIDERS.map((p) => ({ id: p.id, label: p.label }))} />
      <div className="mt-3 space-y-3 text-sm">
        {tab === 'mock' && <p className="text-slate-600">The demo store lives inside Sahay (seeded orders and products). Use it as a sandbox to try everything before connecting a real store.</p>}
        {tab === 'shopify' && (
          <>
            <p className="text-slate-600">Orders, products and abandoned checkouts come from Shopify's Admin API. Create a custom app with orders + products scopes and paste its token.</p>
            <div className="grid grid-cols-2 gap-2">{input('shop_domain', 'yourstore.myshopify.com')}{input('access_token', current.access_token ? '•••• saved' : 'Admin API token', true)}</div>
          </>
        )}
        {tab === 'woocommerce' && (
          <>
            <p className="text-slate-600">WooCommerce → Settings → Advanced → REST API → add a key with read/write access.</p>
            <div className="grid gap-2">{input('site_url', 'https://yourstore.in')}
              <div className="grid grid-cols-2 gap-2">{input('consumer_key', current.consumer_key ? '•••• saved' : 'Consumer key (ck_…)', true)}{input('consumer_secret', current.consumer_secret ? '•••• saved' : 'Consumer secret (cs_…)', true)}</div></div>
          </>
        )}
        {tab === 'custom' && (
          <>
            <p className="text-slate-600">Your own website connects in levels. Start in 5 minutes, add more when you're ready:</p>
            <ol className="space-y-1.5 text-xs text-slate-600">
              <li><b>1 · Snippet (5 min):</b> paste the widget script or <code>npm i @sahay/widget</code>. The AI answers FAQs, captures leads and opens tickets.</li>
              <li><b>2 · Identify + events (~1 hour):</b> sign logged-in customers and send order/product events from your server. The AI knows who's chatting and gives live order status.</li>
              <li><b>3 · Actions (~half a day):</b> add one endpoint with our SDK (getOrder, checkStock, createReturn, …). The AI can do exchanges, returns and cancellations, still under your rules.</li>
            </ol>
            <Field label="Your actions endpoint (level 3, optional)" hint="Every request is signed with your secret key and timestamped. Leave empty for levels 1–2.">
              {input('endpoint_url', 'https://yoursite.in/sahay')}
            </Field>
            <a href="/docs#levels" target="_blank" className="text-xs font-medium text-brand-700 underline">How to set this up (with Node, Python and PHP examples) →</a>
          </>
        )}
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>{tab === active || !PROVIDERS.find((p) => p.id === tab) ? 'Save' : `Use ${PROVIDERS.find((p) => p.id === tab)?.label}`}</Button>
          {save.isSuccess && <span className="text-xs text-emerald-700">Saved. Credentials are stored encrypted.</span>}
          {save.isError && <span className="text-xs text-red-600">{(save.error as Error).message}</span>}
        </div>
      </div>
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
          <div className="flex gap-3 text-xs">
            <a href="/docs" target="_blank" className="font-medium text-brand-700 underline">Developer docs →</a>
            <a href="/api-docs" target="_blank" className="font-medium text-brand-700 underline">API reference →</a>
          </div>
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
          <p>Your server tells Sahay what happened: <code>order.placed</code>, <code>order.shipped</code>, <code>order.delivered</code>, <code>order.cancelled</code>, <code>product.updated</code>, <code>customer.updated</code>, <code>cart.updated</code>, <code>exchange.shipped</code>. Shipping updates reach the customer on WhatsApp and join their open ticket. Repeated event ids are ignored.</p>
          <Code>{example}</Code>
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

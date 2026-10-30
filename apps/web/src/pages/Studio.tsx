import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Eye, Mic, MicOff, Phone, PhoneOff, Plus, RefreshCw, Send, Trash2 } from 'lucide-react'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, cx, Field, inputCls, PageHeader, Spinner, Tabs } from '../components/ui'
import { api } from '../lib/api'
import { humanize } from '../lib/format'

const copy = (t: string) => navigator.clipboard.writeText(t)

function Code({ children }: { children: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 pr-10 text-[11px] leading-relaxed text-slate-100 scroll-thin">{children}</pre>
      <button title="Copy" onClick={() => copy(children)} className="absolute right-2 top-2 rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"><Copy className="h-3.5 w-3.5" /></button>
    </div>
  )
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-0.5" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}{hint && <span className="block text-[11px] text-slate-400">{hint}</span>}</span>
    </label>
  )
}

export default function Studio() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['studio'], queryFn: () => api('/v1/studio') })
  const [tab, setTab] = useState('persona')
  const [p, setP] = useState<any>(null)
  useEffect(() => { if (data) setP(data.profile) }, [data])
  const save = useMutation({
    mutationFn: (body: any) => api('/v1/studio', { method: 'PUT', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['studio'] }),
  })
  if (isLoading || !p) return <Spinner />
  const setIn = (k: string, field: string, v: any) => setP({ ...p, [k]: { ...p[k], [field]: v } })
  const saveAll = () => save.mutate({ agent_name: p.agent_name, persona: p.persona, skills: p.skills, custom_tools: p.custom_tools, sales: p.sales, voice: p.voice, widget: p.widget })
  return (
    <>
      <PageHeader title="Agent Studio" subtitle={`One Agent Profile for ${data.tenant.name}: the website widget, Reply Now email chat, WhatsApp and phone calls all use it.`}
        actions={<>{save.isSuccess && <span className="text-xs text-emerald-700">Saved</span>}{save.isError && <span className="text-xs text-red-600">{(save.error as Error).message}</span>}
          <Button onClick={saveAll} loading={save.isPending}>Save changes</Button></>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'persona', label: 'Persona & sales' }, { id: 'skills', label: 'Skills & tools' }, { id: 'widget', label: 'Website widget' },
        { id: 'voice', label: 'Voice agent' }, { id: 'playground', label: 'Playground' },
      ]} />
      <div className="mt-4">
        {tab === 'persona' && <PersonaTab p={p} setP={setP} setIn={setIn} tenant={data.tenant} />}
        {tab === 'skills' && <SkillsTab p={p} setP={setP} />}
        {tab === 'widget' && <WidgetTab p={p} setIn={setIn} data={data} />}
        {tab === 'voice' && <VoiceTab p={p} setIn={setIn} data={data} />}
        {tab === 'playground' && (
          <Card title="Playground" action={<a className="text-xs text-brand-700 underline" href={`/store/${data.site_key}`} target="_blank">Open on the demo storefront ↗</a>}>
            <p className="mb-3 text-sm text-slate-500">This is the exact widget your customers see, running against your live Agent Profile, knowledge and store data. Save changes first to test them.</p>
            <iframe title="Widget playground" src={`${data.chat_url}?playground=1`} className="h-[600px] w-full max-w-md rounded-xl border border-slate-200 shadow-sm" />
          </Card>
        )}
      </div>
    </>
  )
}

function PersonaTab({ p, setP, setIn, tenant }: { p: any; setP: (v: any) => void; setIn: (k: string, f: string, v: any) => void; tenant: any }) {
  const s = p.sales
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Persona">
        <div className="space-y-3">
          <Field label="Agent name" hint="How the AI introduces itself on chat and calls."><input className={inputCls} value={p.agent_name || ''} onChange={(e) => setP({ ...p, agent_name: e.target.value })} /></Field>
          <Field label="Persona notes" hint="Anything extra about how it should behave: what to push, what to avoid, house phrases.">
            <textarea className={inputCls} rows={5} value={p.persona || ''} onChange={(e) => setP({ ...p, persona: e.target.value })} placeholder="e.g. Always suggest the gift box for birthdays. Never compare us with other brands." />
          </Field>
          <Field label="Brand voice (from Settings)"><div className="rounded-lg bg-slate-50 p-2.5 text-sm text-slate-600">{tenant.brand_voice || '—'}</div></Field>
          <Link to="/settings" className="text-xs font-medium text-brand-700 underline">Edit brand voice in Settings →</Link>
        </div>
      </Card>
      <Card title="Sales settings">
        <div className="space-y-3">
          <Field label="Ideal customer (ICP)" hint="Used for lead finding and scoring."><textarea className={inputCls} rows={3} value={s.icp || ''} onChange={(e) => setIn('sales', 'icp', e.target.value)} placeholder="e.g. Corporate HR teams ordering festive gifts, boutique gift shops in metro cities" /></Field>
          <Field label="What you offer leads"><input className={inputCls} value={s.offer || ''} onChange={(e) => setIn('sales', 'offer', e.target.value)} placeholder="e.g. Bulk gifting from 50 units with custom packaging" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Booking link (optional)"><input className={inputCls} value={s.booking_url || ''} onChange={(e) => setIn('sales', 'booking_url', e.target.value)} placeholder="https://cal.com/…" /></Field>
            <Field label="Email sender name"><input className={inputCls} value={s.sender_name || ''} onChange={(e) => setIn('sales', 'sender_name', e.target.value)} placeholder={`Team ${tenant.name}`} /></Field>
            <Field label="Follow up after (days)"><input type="number" className={inputCls} value={s.follow_up_days ?? 3} onChange={(e) => setIn('sales', 'follow_up_days', Number(e.target.value))} /></Field>
          </div>
          <Toggle checked={s.capture_leads} onChange={(v) => setIn('sales', 'capture_leads', v)} label="Capture leads in chat" hint="When a visitor shows buying interest, the AI asks for name + phone/email and creates a lead." />
          <Toggle checked={s.save_the_order} onChange={(v) => setIn('sales', 'save_the_order', v)} label="Save-the-order offers" hint="Before cancelling an unshipped order, offer one fix (faster delivery, swap, small coupon) — then cancel if they still want to." />
          <Toggle checked={s.cart_reminders !== false} onChange={(v) => setIn('sales', 'cart_reminders', v)} label="Abandoned-cart reminders" hint="One gentle WhatsApp reminder when checkout is left without ordering." />
          {s.cart_reminders !== false && <Field label="Remind after (hours)"><input type="number" min={1} className={inputCls} value={s.cart_reminder_hours ?? 1} onChange={(e) => setIn('sales', 'cart_reminder_hours', Number(e.target.value))} /></Field>}
          <Toggle checked={s.newsletter !== false} onChange={(v) => setIn('sales', 'newsletter', v)} label="Monthly newsletter draft" hint="On the 1st, the AI drafts an email newsletter for your approval." />
        </div>
      </Card>
    </div>
  )
}

const emptyTool = { name: '', description: '', when_to_use: '', url: 'https://', method: 'POST', parameters: [], enabled: true }

function SkillsTab({ p, setP }: { p: any; setP: (v: any) => void }) {
  const groups = ['sales', 'support']
  const tools: any[] = p.custom_tools || []
  const setTool = (i: number, t: any) => setP({ ...p, custom_tools: tools.map((x, j) => (j === i ? t : x)) })
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Skills">
        <p className="mb-3 text-xs text-slate-500">The AI first picks a skill for each message; each skill limits which tools it may use. Turn off what this business doesn't need.</p>
        {groups.map((g) => (
          <div key={g} className="mb-4">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{g}</div>
            <div className="space-y-2">
              {p.skill_catalog.filter((s: any) => s.group === g).map((s: any) => (
                <label key={s.id} className={cx('flex items-start gap-2.5 rounded-lg border p-2.5', p.skills[s.id] ? 'border-brand-200 bg-brand-50/40' : 'border-slate-200')}>
                  <input type="checkbox" className="mt-1" checked={!!p.skills[s.id]} onChange={(e) => setP({ ...p, skills: { ...p.skills, [s.id]: e.target.checked } })} />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-800">{s.label}</div>
                    <div className="text-xs text-slate-500">{s.description}</div>
                    {s.tools.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{s.tools.map((t: string) => <Badge key={t}>{t}</Badge>)}</div>}
                  </div>
                </label>
              ))}
            </div>
          </div>
        ))}
      </Card>
      <Card title="Custom tools" action={<Button size="sm" variant="secondary" onClick={() => setP({ ...p, custom_tools: [...tools, { ...emptyTool }] })}><Plus className="h-3.5 w-3.5" />Add tool</Button>}>
        <p className="mb-3 text-xs text-slate-500">Plug in your own API. The agent calls it live (POST JSON <code>{'{tool, args, customer}'}</code>), signed with your secret key in <code>X-Sahay-Signature</code>. HTTPS only.</p>
        {!tools.length && <div className="rounded-lg border border-dashed p-4 text-center text-xs text-slate-400">No custom tools yet — e.g. "check_delivery_pincode" or "get_bulk_quote".</div>}
        <div className="space-y-3">{tools.map((t, i) => <ToolEditor key={i} t={t} onChange={(v) => setTool(i, v)} onRemove={() => setP({ ...p, custom_tools: tools.filter((_, j) => j !== i) })} />)}</div>
      </Card>
    </div>
  )
}

function ToolEditor({ t, onChange, onRemove }: { t: any; onChange: (v: any) => void; onRemove: () => void }) {
  const [args, setArgs] = useState('{}')
  const test = useMutation({ mutationFn: () => api('/v1/studio/tools/test', { body: { tool: t, args: JSON.parse(args || '{}') } }) })
  const params: any[] = t.parameters || []
  const set = (k: string, v: any) => onChange({ ...t, [k]: v })
  const setParam = (i: number, k: string, v: any) => set('parameters', params.map((x, j) => (j === i ? { ...x, [k]: v } : x)))
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="mb-2 flex items-center gap-2">
        <input className={inputCls + ' font-mono'} placeholder="tool_name" value={t.name} onChange={(e) => set('name', e.target.value)} />
        <label className="flex items-center gap-1 text-xs whitespace-nowrap"><input type="checkbox" checked={t.enabled !== false} onChange={(e) => set('enabled', e.target.checked)} />On</label>
        <button onClick={onRemove} className="text-slate-400 hover:text-red-600" title="Remove"><Trash2 className="h-4 w-4" /></button>
      </div>
      <div className="space-y-2">
        <input className={inputCls} placeholder="What it does (shown to the AI)" value={t.description || ''} onChange={(e) => set('description', e.target.value)} />
        <input className={inputCls} placeholder="When to use it" value={t.when_to_use || ''} onChange={(e) => set('when_to_use', e.target.value)} />
        <div className="flex gap-2">
          <select className={inputCls + ' w-24'} value={t.method || 'POST'} onChange={(e) => set('method', e.target.value)}><option>POST</option><option>GET</option></select>
          <input className={inputCls} placeholder="https://api.yourstore.in/tool" value={t.url || ''} onChange={(e) => set('url', e.target.value)} />
        </div>
        <div className="text-[11px] font-medium text-slate-500">Parameters</div>
        {params.map((pa, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <input className={inputCls + ' w-32 font-mono'} placeholder="name" value={pa.name || ''} onChange={(e) => setParam(i, 'name', e.target.value)} />
            <select className={inputCls + ' w-24'} value={pa.type || 'string'} onChange={(e) => setParam(i, 'type', e.target.value)}><option>string</option><option>number</option><option>boolean</option></select>
            <input className={inputCls} placeholder="description" value={pa.description || ''} onChange={(e) => setParam(i, 'description', e.target.value)} />
            <label className="flex items-center gap-1 text-[11px]"><input type="checkbox" checked={!!pa.required} onChange={(e) => setParam(i, 'required', e.target.checked)} />req</label>
            <button className="text-slate-400 hover:text-red-600" onClick={() => set('parameters', params.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <Button size="sm" variant="ghost" onClick={() => set('parameters', [...params, { name: '', type: 'string', description: '', required: false }])}><Plus className="h-3 w-3" />Parameter</Button>
        <div className="flex gap-2 border-t pt-2">
          <input className={inputCls + ' font-mono text-xs'} value={args} onChange={(e) => setArgs(e.target.value)} placeholder='{"pincode": "110001"}' />
          <Button size="sm" variant="secondary" onClick={() => test.mutate()} loading={test.isPending}>Test</Button>
        </div>
        {test.data && <Code>{JSON.stringify(test.data, null, 2)}</Code>}
        {test.isError && <div className="text-xs text-red-600">{(test.error as Error).message}</div>}
      </div>
    </div>
  )
}

function WidgetTab({ p, setIn, data }: { p: any; setIn: (k: string, f: string, v: any) => void; data: any }) {
  const [inst, setInst] = useState('script')
  const [secret, setSecret] = useState<string | null>(null)
  const reveal = useMutation({ mutationFn: () => api('/v1/studio/secret'), onSuccess: (r) => setSecret(r.secret_key) })
  const rotate = useMutation({ mutationFn: () => api('/v1/studio/secret/rotate', { method: 'POST' }), onSuccess: (r) => setSecret(r.secret_key) })
  const w = p.widget
  const key = data.site_key
  const base = data.public_base_url
  const snippets: Record<string, string> = {
    script: `<script src="${data.loader_url}" data-site-key="${key}" async></script>`,
    npm: `npm i @sahay/widget\n\nimport { init } from '@sahay/widget'\n\ninit({\n  siteKey: '${key}',\n  host: '${base}',\n  user: { email: customer.email, name: customer.name },  // optional, logged-in shoppers\n  userHash,                                             // from your server, see below\n  context: { productSku: 'CHM-SET-07' },               // optional page context\n})`,
    react: `import { SahayChat } from '@sahay/widget/react'\n\nexport default function Layout({ children, customer, userHash }) {\n  return (<>\n    {children}\n    <SahayChat siteKey="${key}" host="${base}" user={customer} userHash={userHash} />\n  </>)\n}`,
    shopify: `1. Install the Sahay app embed (integrations/shopify) on your store.\n2. Online Store → Themes → Customize → App embeds → turn on "Sahay chat".\n3. Paste your site key: ${key}\n4. (Optional) paste your secret key so logged-in customers are recognised automatically.\nThe embed passes the product being viewed and the cart as page context.`,
    wordpress: `1. Upload integrations/wordpress/sahay-chat.php to wp-content/plugins/ and activate "Sahay Chat".\n2. Settings → Sahay Chat: paste site key ${key} and your secret key.\n3. Logged-in WooCommerce customers are signed automatically; product pages pass the SKU as context.`,
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Card title="Install on your website">
          <div className="mb-3 flex items-center gap-2 text-sm">Site key <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{key}</code>
            <button onClick={() => copy(key)} className="text-slate-400 hover:text-slate-700"><Copy className="h-3.5 w-3.5" /></button>
            <span className="text-[11px] text-slate-400">public — only works on your allowed domains</span></div>
          <Tabs value={inst} onChange={setInst} tabs={[{ id: 'script', label: 'Script tag' }, { id: 'npm', label: 'npm' }, { id: 'react', label: 'React' }, { id: 'shopify', label: 'Shopify' }, { id: 'wordpress', label: 'WordPress' }]} />
          <div className="mt-3"><Code>{snippets[inst]}</Code></div>
        </Card>
        <Card title="Page context">
          <p className="mb-2 text-xs text-slate-500">Tell the agent what the visitor is looking at, so "is this in stock?" just works. Page URL and title are sent automatically.</p>
          <Code>{`Sahay('setContext', {\n  productSku: 'CHM-SET-07',\n  cart: [{ sku: 'CHM-BRC-01', qty: 1 }],\n})\nSahay('open')   // open the chat from your own button`}</Code>
        </Card>
      </div>
      <div className="space-y-4">
        <Card title="Security">
          <div className="space-y-3">
            <Field label="Allowed domains (one per line)" hint="The widget only loads on these sites (checked on the server and enforced by the browser). Leave empty to allow any site while testing.">
              <textarea className={inputCls + ' font-mono text-xs'} rows={4} value={(w.allowed_domains || []).join('\n')} onChange={(e) => setIn('widget', 'allowed_domains', e.target.value.split('\n'))} placeholder={'looploot.in\n*.myshopify.com'} />
            </Field>
            {!(w.allowed_domains || []).filter((d: string) => d.trim()).length && <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">No domains set: any website could embed your widget. Add your domain before going live.</div>}
            <Toggle checked={w.require_otp_for_orders} onChange={(v) => setIn('widget', 'require_otp_for_orders', v)} label="Ask anonymous visitors for a phone OTP before showing order details" hint="Signed-in customers (verified with your secret key) skip this." />
            <Field label="Launcher text"><input className={inputCls} value={w.launcher_text || ''} onChange={(e) => setIn('widget', 'launcher_text', e.target.value)} /></Field>
            <Toggle checked={w.bot_check !== false} onChange={(v) => setIn('widget', 'bot_check', v)} label="Bot check for new chats" hint="Each new visitor's browser solves a tiny invisible puzzle (or Cloudflare Turnstile, if its keys are set) before the chat starts." />
            <div className="grid grid-cols-2 gap-3">
              <Field label="Max messages per visitor / day"><input type="number" className={inputCls} value={w.session_message_cap ?? 40} onChange={(e) => setIn('widget', 'session_message_cap', Number(e.target.value))} /></Field>
              <Field label="Max chat messages per day (all visitors)" hint="Caps AI spend if a bot gets through."><input type="number" className={inputCls} value={w.daily_message_cap ?? 400} onChange={(e) => setIn('widget', 'daily_message_cap', Number(e.target.value))} /></Field>
            </div>
          </div>
        </Card>
        <Card title="Identity verification (logged-in shoppers)">
          <p className="mb-2 text-xs text-slate-500">Your server signs the shopper's email (or phone) with your <b>secret key</b>; the widget passes it as <code>userHash</code>. Signed shoppers see their orders without an OTP. <b>Never put the secret key in the browser.</b></p>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{secret || p.secret_key_hint || '—'}</code>
            {secret && <button onClick={() => copy(secret)} className="text-slate-400 hover:text-slate-700"><Copy className="h-3.5 w-3.5" /></button>}
            <Button size="sm" variant="secondary" onClick={() => reveal.mutate()} loading={reveal.isPending}><Eye className="h-3.5 w-3.5" />Reveal</Button>
            <Button size="sm" variant="secondary" loading={rotate.isPending} onClick={() => { if (confirm('Rotate the secret key? Your website, voice pipeline and custom tools must switch to the new key.')) rotate.mutate() }}><RefreshCw className="h-3.5 w-3.5" />Rotate</Button>
            {(reveal.isError || rotate.isError) && <span className="text-xs text-red-600">{((reveal.error || rotate.error) as Error).message}</span>}
          </div>
          <div className="space-y-2">
            <div className="text-[11px] font-medium text-slate-500">Node.js</div>
            <Code>{`import crypto from 'crypto'\nconst userHash = crypto.createHmac('sha256', process.env.SAHAY_SECRET_KEY)\n  .update(customer.email.trim().toLowerCase()).digest('hex')`}</Code>
            <div className="text-[11px] font-medium text-slate-500">Python</div>
            <Code>{`import hmac, hashlib, os\nuser_hash = hmac.new(os.environ["SAHAY_SECRET_KEY"].encode(),\n                     customer.email.strip().lower().encode(), hashlib.sha256).hexdigest()`}</Code>
          </div>
        </Card>
      </div>
    </div>
  )
}

// livekit-client is large; only load it when someone opens a real-voice test call
const LiveKitCall = lazy(() => import('../components/LiveKitCall').then((m) => ({ default: m.LiveKitCall })))

function VoiceTab({ p, setIn, data }: { p: any; setIn: (k: string, f: string, v: any) => void; data: any }) {
  const v = p.voice
  const base = data.public_base_url
  return (
    <div className="space-y-4">
    <VoiceProvider provider={v.provider || 'livekit'} onProvider={(x) => setIn('voice', 'provider', x)} />
    <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
      <div className="space-y-4">
        <Card title="Voice settings">
          <div className="space-y-3">
            <Toggle checked={v.enabled} onChange={(x) => setIn('voice', 'enabled', x)} label="Voice agent on" />
            <div className="grid grid-cols-2 gap-3">
              <Field label="Language"><select className={inputCls} value={v.language || 'hinglish'} onChange={(e) => setIn('voice', 'language', e.target.value)}><option value="hinglish">Hinglish</option><option value="hi">Hindi</option><option value="en">English</option></select></Field>
              <Field label="Business hours"><input className={inputCls} value={v.business_hours || ''} onChange={(e) => setIn('voice', 'business_hours', e.target.value)} /></Field>
              <Field label="Transfer number" hint="Warm-transfer to a human."><input className={inputCls} value={v.transfer_number || ''} onChange={(e) => setIn('voice', 'transfer_number', e.target.value)} placeholder="+91…" /></Field>
              <Field label="Voice"><input className={inputCls} value={v.voice || ''} onChange={(e) => setIn('voice', 'voice', e.target.value)} /></Field>
            </div>
            <Field label="Greeting" hint="Leave empty for an automatic greeting that uses the caller's name."><input className={inputCls} value={v.greeting || ''} onChange={(e) => setIn('voice', 'greeting', e.target.value)} /></Field>
            <Field label="Phone numbers for this business (one per line)" hint="Calls to these numbers reach this agent."><textarea className={inputCls + ' font-mono text-xs'} rows={2} value={(v.numbers || []).join('\n')} onChange={(e) => setIn('voice', 'numbers', e.target.value.split('\n'))} /></Field>
            <Field label="Voice pipeline webhook (Naaradh)" hint="We POST due outbound calls (callbacks, COD confirmations) here."><input className={inputCls} value={v.webhook_url || ''} onChange={(e) => setIn('voice', 'webhook_url', e.target.value)} placeholder="https://naaradh…/dial" /></Field>
            <Toggle checked={v.cod_calls} onChange={(x) => setIn('voice', 'cod_calls', x)} label="Call COD customers to confirm before shipping" hint="In addition to the WhatsApp confirmation." />
          </div>
        </Card>
        <Card title="Voice API (for Naaradh)">
          <p className="mb-2 text-xs text-slate-500">Header <code>X-Sahay-Key: &lt;secret key&gt;</code>. Same skills, tools and policy rules as chat.</p>
          <Code>{`POST ${base}/v1/voice/calls/start   {direction, from_number, to_number, purpose?, call_id?}
  → {call_id, greeting, system_prompt, tools, context}
POST ${base}/v1/voice/calls/{id}/turn  {text}        → {say, end_call, transfer_to}
POST ${base}/v1/voice/calls/{id}/tool  {name, args}  → tool result (policy-gated)
POST ${base}/v1/voice/calls/{id}/end   {transcript?} → {summary, outcome, next_step}
GET  ${base}/v1/voice/tasks                          → due outbound calls`}</Code>
        </Card>
      </div>
      <TestCall />
    </div>
    </div>
  )
}

const SARVAM_FIELDS: [string, string, string][] = [
  ['api_key', 'API key', 'Settings → API Key in Sarvam'],
  ['org_id', 'Org ID', 'from the dashboard URL'],
  ['workspace_id', 'Workspace ID', 'from the dashboard URL'],
  ['app_id', 'Agent (app) ID', 'the agent you created'],
  ['app_version', 'Agent version', 'committed version, e.g. 1'],
  ['connection_id', 'Connection ID', 'telephony connection'],
  ['agent_phone_number', 'Agent phone number', '+91… rented or connected number'],
]

function VoiceProvider({ provider, onProvider }: { provider: string; onProvider: (v: string) => void }) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['sarvam'], queryFn: () => api('/v1/voice/sarvam') })
  const [cfg, setCfg] = useState<Record<string, string>>({})
  useEffect(() => { if (data?.config) setCfg((c) => ({ ...Object.fromEntries(Object.entries(data.config).map(([k, x]) => [k, x == null ? '' : String(x)])), ...c })) }, [data])
  const save = useMutation({
    mutationFn: () => api('/v1/integrations/sarvam', { method: 'PUT', body: {  // blank fields keep the saved value
      ...Object.fromEntries(Object.entries(cfg).filter(([, x]) => x !== '' && x != null)), app_version: cfg.app_version ? Number(cfg.app_version) : undefined } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sarvam'] }); setCfg((c) => ({ ...c, api_key: '' })) },
  })
  const [phone, setPhone] = useState('')
  const test = useMutation({ mutationFn: () => api('/v1/voice/sarvam/test-call', { body: { phone } }) })
  const connected = data?.connected || {}
  const allOk = Object.values(connected).length > 0 && Object.values(connected).every(Boolean)
  return (
    <Card title="Phone provider" action={provider === 'livekit' ? null : allOk ? <Badge tone="green">Sarvam connected</Badge> : <Badge tone="amber">not connected</Badge>}>
      <div className="mb-3 flex flex-wrap gap-2">
        {[['livekit', 'Sarvam voice on LiveKit (recommended)'], ['sarvam', 'Sarvam Voice Agents'], ['naaradh', 'Your own pipeline (Naaradh API)']].map(([id, label]) => (
          <button key={id} onClick={() => onProvider(id)} className={cx('rounded-lg border px-3 py-1.5 text-sm', provider === id ? 'border-brand-500 bg-brand-50 font-medium text-brand-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50')}>{label}</button>
        ))}
        <span className="self-center text-[11px] text-slate-400">Save the choice with "Save changes" at the top.</span>
      </div>
      {provider === 'livekit' ? <LiveKitSetup /> : provider !== 'sarvam' ? (
        <p className="text-sm text-slate-600">Your pipeline calls our Voice API below (start → turn/tool → end) with your secret key, and we POST due outbound calls to the webhook you set. Same brain, tools and rules.</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
          <div className="space-y-3">
            <p className="text-xs text-slate-500">Sarvam rents the phone line and does the listening and speaking (Hindi / Hinglish built in). We stay the brain: your data, tools, rules and the customer timeline. About ₹3–3.5 per minute plus telephony; browser test calls below are free.</p>
            <div className="grid grid-cols-2 gap-2">
              {SARVAM_FIELDS.map(([k, label, hint]) => (
                <Field key={k} label={label} hint={hint}>
                  <input className={inputCls} type={k === 'api_key' ? 'password' : 'text'} value={cfg[k] || ''}
                    placeholder={k === 'api_key' && connected.api_key ? '•••• saved' : ''} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })} />
                </Field>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">{SARVAM_FIELDS.map(([k, label]) => <Badge key={k} tone={connected[k] ? 'green' : 'slate'}>{connected[k] ? '✓' : '○'} {label}</Badge>)}</div>
            <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>Save Sarvam connection</Button>
            {save.isError && <div className="text-xs text-red-600">{(save.error as Error).message}</div>}
            {save.isSuccess && <div className="text-xs text-emerald-700">Saved (keys are stored encrypted).</div>}
          </div>
          <SarvamSetup data={data} phone={phone} setPhone={setPhone} test={test} />
        </div>
      )}
    </Card>
  )
}

function LiveKitSetup() {
  const { data } = useQuery({ queryKey: ['livekit'], queryFn: () => api('/v1/voice/livekit') })
  const [phone, setPhone] = useState('')
  const test = useMutation({ mutationFn: () => api('/v1/voice/livekit/test-call', { body: { phone } }) })
  if (!data) return <Spinner />
  const checks: [boolean, string, string][] = [
    [data.configured, 'LiveKit project', 'LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET on the server'],
    [data.sarvam_key, 'Sarvam API key', 'SARVAM_API_KEY on the server, or this business\'s key under Sarvam Voice Agents'],
    [data.sip_trunk, 'Outbound SIP trunk', 'LIVEKIT_SIP_TRUNK_ID: only needed to dial real phones'],
  ]
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
      <div className="space-y-3">
        <p className="text-xs text-slate-500">Our own voice agent: LiveKit carries the call (browser or phone), Sarvam listens (Saaras) and speaks (Bulbul{data.voice?.speaker ? `, voice ${data.voice.speaker}` : ''}), Gemini picks the words, and every action goes through your rules. Browser test calls need no phone number.</p>
        <div className="space-y-1.5">
          {checks.map(([ok, label, hint]) => (
            <div key={label} className="flex items-start gap-2 text-sm"><Badge tone={ok ? 'green' : 'slate'}>{ok ? '✓' : '○'}</Badge><div><div className="font-medium">{label}</div><div className="text-[11px] text-slate-500">{hint}</div></div></div>
          ))}
        </div>
        <div className="text-[11px] text-slate-500">Worker name: <code>{data.agent_name}</code>. Numbers routed here: {data.numbers?.length ? data.numbers.join(', ') : 'none yet (add them in Voice settings)'}.</div>
      </div>
      <div className="space-y-2 rounded-lg border border-slate-100 bg-slate-50/60 p-3 text-xs text-slate-600">
        <div className="text-sm font-semibold text-slate-800">Phone calls (optional)</div>
        <div>1. Get a number from a SIP provider (Plivo, Twilio, Telnyx, Exotel vSIP). Indian numbers usually need KYC.</div>
        <div>2. Run <code>scripts/livekit_sip.py inbound --numbers +91…</code> so calls reach this agent, and add the number in Voice settings.</div>
        <div>3. Run <code>scripts/livekit_sip.py outbound …</code> and set <code>LIVEKIT_SIP_TRUNK_ID</code> so the agent can dial out (callbacks, COD checks).</div>
        <div className="flex gap-2 pt-1">
          <input className={inputCls} placeholder="+91 your phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
          <Button size="sm" onClick={() => test.mutate()} loading={test.isPending} disabled={!phone.trim() || !data.sip_trunk}><Phone className="h-3.5 w-3.5" />Call me</Button>
        </div>
        {test.isSuccess && <div className="text-emerald-700">Dialling… the call appears on the Calls page.</div>}
        {test.isError && <div className="text-red-600">{(test.error as Error).message}</div>}
      </div>
    </div>
  )
}

function SarvamSetup({ data, phone, setPhone, test }: { data: any; phone: string; setPhone: (v: string) => void; test: any }) {
  if (!data) return <Spinner />
  const Step = ({ n, title, children }: { n: number; title: string; children?: any }) => (
    <div className="flex gap-2.5"><div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-600 text-[11px] font-bold text-white">{n}</div>
      <div className="min-w-0 flex-1"><div className="text-sm font-medium">{title}</div>{children && <div className="mt-1 space-y-1.5 text-xs text-slate-600">{children}</div>}</div></div>
  )
  return (
    <div className="space-y-3 rounded-lg border border-slate-100 bg-slate-50/60 p-3">
      <div className="text-sm font-semibold">Set up the Sarvam agent (one time)</div>
      <Step n={1} title="Get a number">Rent one in Sarvam (₹59 / 30 days) or connect Exotel / Plivo / Vobiz. Indian numbers can need business verification, so do this a few days early.</Step>
      <Step n={2} title="Create an agent and paste this prompt"><Code>{data.prompt}</Code></Step>
      <Step n={3} title="Add these agent variables">
        <div className="flex flex-wrap gap-1">{(data.variables || []).map((x: string) => <code key={x} className="rounded bg-white px-1.5 py-0.5 ring-1 ring-slate-200">{x}</code>)}</div>
      </Step>
      <Step n={4} title="On-start hook (loads the caller before hello)">
        <Code>{data.urls?.on_start}</Code>
        <div>Method POST, auth: Bearer token = your secret key (store it in Sarvam → Settings → Secrets). Send the User Identifier and Interaction ID in the body; map the returned fields into the variables above.</div>
      </Step>
      <Step n={5} title="Add the API tools">
        <div>Paste each cURL into Sarvam's tool editor. Response template: <code>{data.response_template}</code>, max wait {data.max_wait_s}s. Our tools enforce your rules, so the voice AI can't talk past them.</div>
        <div className="space-y-1">
          {(data.tools || []).map((t: any) => (
            <div key={t.name} className="flex items-start justify-between gap-2 rounded bg-white px-2 py-1.5 ring-1 ring-slate-200">
              <div className="min-w-0"><code className="text-[11px] font-semibold">{t.name}</code><div className="truncate text-[11px] text-slate-500" title={t.description}>{t.description}</div></div>
              <Button size="sm" variant="secondary" onClick={() => copy(t.curl)}><Copy className="h-3 w-3" />cURL</Button>
            </div>
          ))}
        </div>
      </Step>
      <Step n={6} title="Deployment webhook (transcript → customer timeline)"><Code>{data.urls?.webhook}</Code><div>Set it as the deployment's webhook URL; the same URL is used for outbound calls automatically.</div></Step>
      <Step n={7} title="Test an outbound call">
        <div className="flex gap-2">
          <input className={inputCls} placeholder="+91 your phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
          <Button size="sm" onClick={() => test.mutate()} loading={test.isPending} disabled={!phone.trim()}><Phone className="h-3.5 w-3.5" />Call me</Button>
        </div>
        {test.isSuccess && <div className="text-emerald-700">Dialling… attempt {test.data?.attempt_id || '—'}. The call appears on the Calls page.</div>}
        {test.isError && <div className="text-red-600">{(test.error as Error).message}</div>}
      </Step>
    </div>
  )
}

type Line = { role: 'agent' | 'caller'; text: string; tools?: string[]; notes?: string[] }

function TestCall() {
  const { data: contacts } = useQuery({ queryKey: ['sim-contacts'], queryFn: () => api('/v1/simulator/contacts') })
  const [phone, setPhone] = useState('')
  const [call, setCall] = useState<any>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [lang, setLang] = useState<'en-IN' | 'hi-IN'>('en-IN')
  const [typed, setTyped] = useState('')
  const [summary, setSummary] = useState<any>(null)
  const [err, setErr] = useState('')
  const recRef = useRef<any>(null)
  const callRef = useRef<any>(null)
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  const { data: lk } = useQuery({ queryKey: ['livekit'], queryFn: () => api('/v1/voice/livekit') })
  const [mode, setMode] = useState<'' | 'livekit' | 'browser'>('')
  const real = (mode || (lk?.configured ? 'livekit' : 'browser')) === 'livekit'

  const speak = (text: string) => new Promise<void>((resolve) => {
    const synth = window.speechSynthesis
    if (!synth) return resolve()
    const u = new SpeechSynthesisUtterance(text)
    const voices = synth.getVoices()
    const hi = voices.find((x) => x.lang === 'hi-IN') || voices.find((x) => x.lang === 'en-IN')
    if (hi) u.voice = hi
    u.lang = hi?.lang || 'hi-IN'
    u.rate = 1.02
    u.onend = () => resolve(); u.onerror = () => resolve()
    synth.speak(u)
  })

  const start = async () => {
    setErr(''); setSummary(null); setLines([])
    try {
      const r = await api('/v1/voice/test/start', { body: { phone: phone || null } })
      setCall(r); callRef.current = r
      setLines([{ role: 'agent', text: r.greeting }])
      await speak(r.greeting)
      if (SR) listen()
    } catch (e: any) { setErr(e.message) }
  }

  const send = async (text: string) => {
    if (!text.trim() || !callRef.current) return
    setLines((l) => [...l, { role: 'caller', text }])
    setBusy(true)
    const filler = setTimeout(() => speak('Ek second, check kar rahi hoon.'), 1200)
    try {
      const r = await api(`/v1/voice/test/${callRef.current.call_id}/turn`, { body: { text } })
      clearTimeout(filler)
      window.speechSynthesis?.cancel()
      const notes = [...(r.whatsapp_sent || []).map((t: string) => `📲 Sent on WhatsApp: ${t}`), ...(r.transfer_to ? [`☎ Transfer to ${r.transfer_to}`] : [])]
      setLines((l) => [...l, { role: 'agent', text: r.say, tools: (r.tools_used || []).map((u: any) => u.tool), notes }])
      await speak(r.say)
      if (r.end_call) { await end(); return }
      if (SR) listen()
    } catch (e: any) { clearTimeout(filler); setErr(e.message) } finally { setBusy(false) }
  }

  const listen = () => {
    if (!SR || !callRef.current) return
    try { recRef.current?.abort() } catch { /* ignore */ }
    const rec = new SR()
    rec.lang = lang; rec.interimResults = false; rec.maxAlternatives = 1
    rec.onresult = (e: any) => { const t = e.results[0][0].transcript; setListening(false); send(t) }
    rec.onerror = () => setListening(false)
    rec.onend = () => setListening(false)
    recRef.current = rec
    setListening(true)
    rec.start()
  }

  const end = async () => {
    try { recRef.current?.abort() } catch { /* ignore */ }
    window.speechSynthesis?.cancel()
    const c = callRef.current
    callRef.current = null; setCall(null); setListening(false)
    if (c) { try { setSummary(await api(`/v1/voice/test/${c.call_id}/end`, { method: 'POST' })) } catch (e: any) { setErr(e.message) } }
  }

  return (
    <Card title={<span className="flex items-center gap-1.5"><Phone className="h-4 w-4 text-brand-600" />Test call in your browser</span>}
      action={!call && (
        <div className="flex gap-1">
          {[['livekit', 'Sarvam voice'], ['browser', 'Browser voice']].map(([id, label]) => (
            <button key={id} onClick={() => setMode(id as any)} disabled={id === 'livekit' && !lk?.configured}
              title={id === 'livekit' && !lk?.configured ? 'Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET on the server' : undefined}
              className={cx('rounded-md px-2 py-1 text-xs', (id === 'livekit') === real ? 'bg-brand-50 font-medium text-brand-700' : 'text-slate-500 hover:bg-slate-50 disabled:opacity-40')}>{label}</button>
          ))}
        </div>
      )}>
      {real ? (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">The real phone pipeline in your browser: LiveKit carries the audio, Sarvam listens and speaks{lk?.voice?.speaker ? ` (voice: ${lk.voice.speaker}, ${lk.voice.target_language_code})` : ''}, and the agent uses the same tools and rules as a call. Allow microphone access when asked.</p>
          <Field label="Call as">
            <select className={inputCls + ' w-72'} value={phone} onChange={(e) => setPhone(e.target.value)}>
              <option value="">Unknown caller (new number)</option>
              {(contacts || []).map((c: any) => <option key={c.customer_phone} value={c.customer_phone}>{c.customer_name} · #{c.order_ref} ({humanize(c.status)})</option>)}
            </select>
          </Field>
          <Suspense fallback={<Spinner />}><LiveKitCall phone={phone} /></Suspense>
        </div>
      ) : <>
      {!SR && <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">This browser has no speech recognition (use Chrome for voice). You can still type what the caller says.</div>}
      {!call ? (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Call as">
            <select className={inputCls + ' w-72'} value={phone} onChange={(e) => setPhone(e.target.value)}>
              <option value="">Unknown caller (new number)</option>
              {(contacts || []).map((c: any) => <option key={c.customer_phone} value={c.customer_phone}>{c.customer_name} · #{c.order_ref} ({humanize(c.status)})</option>)}
            </select>
          </Field>
          <Button onClick={start}><Phone className="h-4 w-4" />Start call</Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="green">● On call</Badge>
          {call.context?.name && <span className="text-xs text-slate-500">Agent knows: {call.context.name}{call.context.orders?.length ? ` · ${call.context.orders.length} orders` : ''}{call.context.open_tickets?.length ? ` · ${call.context.open_tickets.length} open tickets` : ''}</span>}
          <div className="flex-1" />
          {SR && <select className="rounded border px-1.5 py-1 text-xs" value={lang} onChange={(e) => setLang(e.target.value as any)}><option value="en-IN">English / Hinglish</option><option value="hi-IN">हिन्दी</option></select>}
          {SR && <Button size="sm" variant={listening ? 'success' : 'secondary'} onClick={listen} disabled={busy}>{listening ? <><Mic className="h-3.5 w-3.5" />Listening…</> : <><MicOff className="h-3.5 w-3.5" />Tap to talk</>}</Button>}
          <Button size="sm" variant="danger" onClick={end}><PhoneOff className="h-3.5 w-3.5" />End call</Button>
        </div>
      )}
      {err && <div className="mt-2 text-xs text-red-600">{err}</div>}
      {lines.length > 0 && (
        <div className="mt-3 max-h-[420px] space-y-2 overflow-y-auto rounded-lg bg-slate-50 p-3 scroll-thin">
          {lines.map((l, i) => (
            <div key={i} className={cx('flex', l.role === 'caller' ? 'justify-end' : 'justify-start')}>
              <div className={cx('max-w-[80%] rounded-xl px-3 py-2 text-sm', l.role === 'caller' ? 'bg-brand-600 text-white' : 'border border-slate-200 bg-white text-slate-800')}>
                <div className="mb-0.5 text-[10px] opacity-60">{l.role === 'caller' ? 'Caller' : 'Voice agent'}</div>
                {l.text}
                {!!l.tools?.length && <div className="mt-1 flex flex-wrap gap-1">{l.tools.map((t, j) => <Badge key={j} tone="violet">{t}</Badge>)}</div>}
                {l.notes?.map((n, j) => <div key={j} className="mt-1 text-[11px] text-emerald-700">{n}</div>)}
              </div>
            </div>
          ))}
          {busy && <div className="text-xs text-slate-400">Agent is thinking…</div>}
        </div>
      )}
      {call && (
        <div className="mt-2 flex gap-2">
          <input className={inputCls} placeholder="Or type what the caller says…" value={typed} onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { send(typed); setTyped('') } }} />
          <Button variant="secondary" onClick={() => { send(typed); setTyped('') }} disabled={busy}><Send className="h-4 w-4" /></Button>
        </div>
      )}
      {summary && (
        <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm">
          <div className="mb-1 flex items-center gap-2 font-medium text-emerald-900">Call summary <Badge tone="green">{humanize(summary.outcome)}</Badge></div>
          <div className="text-slate-700">{summary.summary}</div>
          {summary.next_step && <div className="mt-1 text-xs text-slate-600">Next step: {summary.next_step}</div>}
          <Link to="/calls" className="mt-2 inline-block text-xs font-medium text-brand-700 underline">See it in Calls →</Link>
        </div>
      )}
      </>}
    </Card>
  )
}

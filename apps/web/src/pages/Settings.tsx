import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Field, inputCls, PageHeader, Spinner } from '../components/ui'
import { api } from '../lib/api'

export default function SettingsPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const [s, setS] = useState<any>(null)
  useEffect(() => { if (data) setS(data.settings) }, [data])
  const save = useMutation({
    mutationFn: () => api('/v1/settings', { method: 'PATCH', body: s }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['me'] }) },
  })
  if (isLoading || !s) return <Spinner />
  const set = (k: string) => (e: any) => setS({ ...s, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'number' ? Number(e.target.value) : e.target.value })
  const widgetKey = data.routes.find((r: any) => r.channel === 'webchat')?.external_key
  const integ = (k: string) => data.integrations.find((i: any) => i.kind === k)?.config || {}
  return (
    <>
      <PageHeader title="Settings" subtitle={`${data.settings.name} · workspace /${data.settings.slug}`} actions={<Button onClick={() => save.mutate()} loading={save.isPending}>Save changes</Button>} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Brand & voice">
          <div className="space-y-3">
            <Field label="What you sell"><textarea className={inputCls} rows={3} value={s.business_description} onChange={set('business_description')} /></Field>
            <Field label="Brand voice" hint="How Sahay should sound. It adapts to Hindi, Hinglish or English automatically."><textarea className={inputCls} rows={3} value={s.brand_voice} onChange={set('brand_voice')} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Default reply mode"><select className={inputCls} value={s.default_autonomy} onChange={set('default_autonomy')}><option value="auto">Auto-send</option><option value="draft">Draft for review</option></select></Field>
              <Field label="SLA (minutes)"><input type="number" className={inputCls} value={s.sla_minutes} onChange={set('sla_minutes')} /></Field>
              <Field label="Wait for message bursts (ms)" hint="Customers send 3 messages in a row on WhatsApp; Sahay waits and answers once."><input type="number" className={inputCls} value={s.debounce_ms} onChange={set('debounce_ms')} /></Field>
              <Field label="Website"><input className={inputCls} value={s.website_url || ''} onChange={set('website_url')} /></Field>
              <Field label="Ticket prefix" hint={`Your tickets are numbered ${(s.ticket_prefix || 'T')}-1042.`}>
                <input className={inputCls} maxLength={5} value={s.ticket_prefix || ''} placeholder="LL"
                  onChange={(e) => setS({ ...s, ticket_prefix: e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 5) })} />
              </Field>
            </div>
          </div>
        </Card>
        <Card title="Owner approvals on WhatsApp">
          <div className="space-y-3">
            <Field label="Owner WhatsApp number"><input className={inputCls} value={s.owner_phone || ''} onChange={set('owner_phone')} placeholder="+91…" /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.owner_wa_approvals} onChange={set('owner_wa_approvals')} />Send approval requests to the owner's WhatsApp with Approve / Reject buttons</label>
          </div>
        </Card>
        <Card title="Web chat widget">
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Brand colour"><input type="color" className="h-9 w-full rounded-lg border" value={s.brand_color} onChange={set('brand_color')} /></Field>
              <Field label="Greeting"><input className={inputCls} value={s.widget_greeting} onChange={set('widget_greeting')} /></Field>
            </div>
            <Link to="/studio" className="block text-sm font-medium text-brand-700 underline">Install, domains &amp; keys are in Agent Studio →</Link>
            <a className="text-xs font-medium text-brand-700 underline" href={`/store/${widgetKey}`} target="_blank">Preview on the demo storefront ↗</a>
          </div>
        </Card>
        <Integrations data={data} integ={integ} />
      </div>
    </>
  )
}

function Integrations({ data, integ }: { data: any; integ: (k: string) => any }) {
  const qc = useQueryClient()
  const [wa, setWa] = useState({ phone_number_id: integ('whatsapp').phone_number_id || '', access_token: integ('whatsapp').access_token || '' })
  const [em, setEm] = useState({ inbound_address: integ('email').inbound_address || '', from: integ('email').from || '', api_key: integ('email').api_key || '' })
  const save = useMutation({ mutationFn: ({ kind, body }: any) => api(`/v1/integrations/${kind}`, { method: 'PUT', body }), onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }) })
  const hook = `${location.origin}/webhooks/whatsapp`
  return (
    <Card title="Channels">
      <div className="space-y-5">
        <div>
          <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">WhatsApp Cloud API {integ('whatsapp').access_token ? <Badge tone="green">connected</Badge> : <Badge>simulated</Badge>}</div>
          <div className="grid grid-cols-2 gap-2">
            <input className={inputCls} placeholder="Phone number ID" value={wa.phone_number_id} onChange={(e) => setWa({ ...wa, phone_number_id: e.target.value })} />
            <input className={inputCls} placeholder="Access token" value={wa.access_token} onChange={(e) => setWa({ ...wa, access_token: e.target.value })} />
          </div>
          <div className="mt-1 text-[11px] text-slate-400">Webhook URL for Meta: {hook}</div>
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => save.mutate({ kind: 'whatsapp', body: wa })}>Save WhatsApp</Button>
        </div>
        <div>
          <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">Email (Resend) {integ('email').api_key ? <Badge tone="green">connected</Badge> : <Badge>simulated</Badge>}</div>
          <div className="grid grid-cols-3 gap-2">
            <input className={inputCls} placeholder="Inbound address" value={em.inbound_address} onChange={(e) => setEm({ ...em, inbound_address: e.target.value })} />
            <input className={inputCls} placeholder="From address" value={em.from} onChange={(e) => setEm({ ...em, from: e.target.value })} />
            <input className={inputCls} placeholder="Resend API key" value={em.api_key} onChange={(e) => setEm({ ...em, api_key: e.target.value })} />
          </div>
          <div className="mt-1 text-[11px] text-slate-400">Inbound webhook: {location.origin}/webhooks/email</div>
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => save.mutate({ kind: 'email', body: em })}>Save email</Button>
        </div>
        <div className="rounded-lg border border-dashed border-slate-300 p-3 text-sm text-slate-600">
          Store connection (Shopify, WooCommerce or your own website) moved to <Link to="/integrations" className="font-medium text-brand-700 underline">Integrations →</Link>
        </div>
        {save.isSuccess && <div className="text-xs text-emerald-700">Saved.</div>}
        <div className="hidden">{data.public_base_url}</div>
      </div>
    </Card>
  )
}

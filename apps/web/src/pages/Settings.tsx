import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Card, Field, inputCls, PageHeader, Spinner } from '../components/ui'
import ChannelConnections from '../components/ChannelConnections'
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
  return (
    <>
      <PageHeader title="Settings" subtitle={`${data.settings.name} · workspace /${data.settings.slug}`} actions={<Button onClick={() => save.mutate()} loading={save.isPending}>Save changes</Button>} />
      <div className="settings-layout">
        <div className="settings-column">
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
        <Card title="Web chat widget">
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Brand colour"><input type="color" className="h-9 w-full rounded-lg border" value={s.brand_color} onChange={set('brand_color')} /></Field>
              <Field label="Greeting"><input className={inputCls} value={s.widget_greeting} onChange={set('widget_greeting')} /></Field>
            </div>
            <Link to="/studio?tab=widget" className="block text-sm font-medium text-brand-700 underline">Website chat setup →</Link>
            <a className="text-xs font-medium text-brand-700 underline" href={`/store/${widgetKey}`} target="_blank">Preview on the demo storefront ↗</a>
          </div>
        </Card>
        </div>
        <div className="settings-column">
        <Card title="Owner approvals on WhatsApp">
          <div className="space-y-3">
            <Field label="Owner WhatsApp number"><input className={inputCls} value={s.owner_phone || ''} onChange={set('owner_phone')} placeholder="+91…" /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.owner_wa_approvals} onChange={set('owner_wa_approvals')} />Send approval requests to the owner's WhatsApp with Approve / Reject buttons</label>
          </div>
        </Card>
        <ChannelConnections integrations={data.integrations} platformEmail={data.platform_email} />
        </div>
      </div>
    </>
  )
}

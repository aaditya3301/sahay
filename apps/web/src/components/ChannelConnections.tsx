import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Badge, Button, Card, Field, inputCls } from './ui'

export default function ChannelConnections({ integrations, showOtherChannels = false }: { integrations: any[]; showOtherChannels?: boolean }) {
  const qc = useQueryClient()
  const config = (kind: string) => integrations.find((item: any) => item.kind === kind)?.config || {}
  const [wa, setWa] = useState({ phone_number_id: config('whatsapp').phone_number_id || '', access_token: config('whatsapp').access_token || '' })
  const [em, setEm] = useState({ inbound_address: config('email').inbound_address || '', from: config('email').from || '', api_key: config('email').api_key || '' })
  const save = useMutation({
    mutationFn: ({ kind, body }: { kind: string; body: any }) => api(`/v1/integrations/${kind}`, { method: 'PUT', body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['integration-health'] }) },
  })
  return <Card title="Customer channels" className="channel-connections">
    <p className="setup-intro">Choose where customers can reach your agent.</p>
    <details className="connection-disclosure">
      <summary><span>WhatsApp</span><Badge tone={config('whatsapp').access_token ? 'green' : 'slate'}>{config('whatsapp').access_token ? 'Configured' : 'Set up'}</Badge></summary>
      <div className="connection-body">
        <p>Use your WhatsApp Business connection details from Meta. The person who manages your business account can help you find them.</p>
        <div className="setup-fields">
          <Field label="Phone number ID" hint="The ID from Meta, rather than your phone number."><input name="whatsapp-phone-number-id" autoComplete="off" className={inputCls} value={wa.phone_number_id} onChange={(e) => setWa({ ...wa, phone_number_id: e.target.value })} /></Field>
          <Field label="Access token"><input name="whatsapp-api-token" type="password" autoComplete="new-password" className={inputCls} value={wa.access_token} onChange={(e) => setWa({ ...wa, access_token: e.target.value })} /></Field>
        </div>
        <details className="dashboard-disclosure"><summary>Webhook address for your developer</summary><div className="connection-copy-row"><code>{location.origin}/webhooks/whatsapp</code><button type="button" className="dashboard-icon-button" aria-label="Copy WhatsApp webhook address" onClick={() => navigator.clipboard.writeText(`${location.origin}/webhooks/whatsapp`)}><Copy size={16} /></button></div></details>
        <Button size="sm" variant="secondary" onClick={() => save.mutate({ kind: 'whatsapp', body: wa })} loading={save.isPending && save.variables?.kind === 'whatsapp'}>Save WhatsApp connection</Button>
        {save.variables?.kind === 'whatsapp' && <SaveState saved={save.isSuccess} error={save.error} />}
      </div>
    </details>
    <details className="connection-disclosure">
      <summary><span>Email</span><Badge tone={config('email').api_key ? 'green' : 'slate'}>{config('email').api_key ? 'Configured' : 'Set up'}</Badge></summary>
      <div className="connection-body">
        <p>Sahay uses Resend for email. Have your sending address, receiving address and Resend API key ready.</p>
        <div className="setup-fields">
          <Field label="Receiving email address"><input className={inputCls} placeholder="help@yourstore.in" value={em.inbound_address} onChange={(e) => setEm({ ...em, inbound_address: e.target.value })} /></Field>
          <Field label="Sending email address"><input className={inputCls} placeholder="support@yourstore.in" value={em.from} onChange={(e) => setEm({ ...em, from: e.target.value })} /></Field>
          <Field label="Resend API key"><input name="resend-api-key" type="password" autoComplete="new-password" className={inputCls} value={em.api_key} onChange={(e) => setEm({ ...em, api_key: e.target.value })} /></Field>
        </div>
        <details className="dashboard-disclosure"><summary>Webhook address for your developer</summary><div className="connection-copy-row"><code>{location.origin}/webhooks/email</code><button type="button" className="dashboard-icon-button" aria-label="Copy email webhook address" onClick={() => navigator.clipboard.writeText(`${location.origin}/webhooks/email`)}><Copy size={16} /></button></div></details>
        <Button size="sm" variant="secondary" onClick={() => save.mutate({ kind: 'email', body: em })} loading={save.isPending && save.variables?.kind === 'email'}>Save email connection</Button>
        {save.variables?.kind === 'email' && <SaveState saved={save.isSuccess} error={save.error} />}
      </div>
    </details>
    {showOtherChannels && <>
      <Link className="connection-destination" to="/studio?tab=widget"><span>Website chat<small>Add the chat widget to your website.</small></span><span aria-hidden="true">→</span></Link>
      <Link className="connection-destination" to="/studio?tab=voice"><span>Phone calls<small>Connect a number for your voice agent.</small></span><span aria-hidden="true">→</span></Link>
    </>}
  </Card>
}

function SaveState({ saved, error }: { saved: boolean; error: Error | null }) {
  if (error) return <p role="alert" className="text-sm text-red-600">{error.message}</p>
  return saved ? <p role="status" className="text-sm text-emerald-700">Connection details saved.</p> : null
}

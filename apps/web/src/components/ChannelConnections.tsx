import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Badge, Button, Card, Field, inputCls } from './ui'

export default function ChannelConnections({ integrations, showOtherChannels = false, platformEmail = false }: { integrations: any[]; showOtherChannels?: boolean; platformEmail?: boolean }) {
  const qc = useQueryClient()
  const config = (kind: string) => integrations.find((item: any) => item.kind === kind)?.config || {}
  const [em, setEm] = useState({ inbound_address: config('email').inbound_address || '', from: config('email').from || '', api_key: config('email').api_key || '' })
  const save = useMutation({
    mutationFn: ({ kind, body }: { kind: string; body: any }) => api(`/v1/integrations/${kind}`, { method: 'PUT', body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['integration-health'] }) },
  })
  return <Card title="Customer channels" className="channel-connections">
    <p className="setup-intro">Choose where customers can reach your agent.</p>
    <TelegramPanel />
    <details className="connection-disclosure">
      <summary><span>Email</span><Badge tone={config('email').api_key || platformEmail ? 'green' : 'slate'}>{config('email').api_key ? 'Configured' : platformEmail ? 'Using Sahay\'s Brevo' : 'Set up'}</Badge></summary>
      <div className="connection-body">
        <p>Sahay sends and receives email through Brevo. Your sending address must be verified in Brevo. Leave the key empty to use Sahay's own Brevo account.</p>
        <div className="setup-fields">
          <Field label="Receiving email address"><input className={inputCls} placeholder="yourstore@reply.yourdomain.in" value={em.inbound_address} onChange={(e) => setEm({ ...em, inbound_address: e.target.value })} /></Field>
          <Field label="Sending email address"><input className={inputCls} placeholder="Your Store <support@yourstore.in>" value={em.from} onChange={(e) => setEm({ ...em, from: e.target.value })} /></Field>
          <Field label="Brevo API key"><input name="brevo-api-key" placeholder="xkeysib-…" type="password" autoComplete="new-password" className={inputCls} value={em.api_key} onChange={(e) => setEm({ ...em, api_key: e.target.value })} /></Field>
        </div>
        <details className="dashboard-disclosure"><summary>Webhook addresses for your developer</summary><div className="connection-copy-row"><code>{location.origin}/webhooks/email</code><button type="button" className="dashboard-icon-button" aria-label="Copy inbound email webhook address" onClick={() => navigator.clipboard.writeText(`${location.origin}/webhooks/email`)}><Copy size={16} /></button></div><div className="connection-copy-row"><code>{location.origin}/webhooks/email/events</code><button type="button" className="dashboard-icon-button" aria-label="Copy email events webhook address" onClick={() => navigator.clipboard.writeText(`${location.origin}/webhooks/email/events`)}><Copy size={16} /></button></div><p className="text-[11px] text-slate-500">Brevo inbound parsing posts to the first, delivery events to the second.</p></details>
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

// Customers reach the agent on Telegram (@ the platform bot). They verify with order ID + the name on the order.
function TelegramPanel() {
  const qc = useQueryClient()
  const { data: tg, isLoading, error } = useQuery({ queryKey: ['telegram'], queryFn: () => api('/v1/telegram'), retry: false })
  const connect = useMutation({ mutationFn: () => api('/v1/telegram/connect', { method: 'POST' }), onSuccess: (d) => { qc.setQueryData(['telegram'], d); qc.invalidateQueries({ queryKey: ['integration-health'] }) } })
  const live = tg?.enabled && tg.webhook_url && tg.webhook_url === tg.webhook_expected
  return <details className="connection-disclosure" open>
    <summary><span>Telegram</span><Badge tone={live ? 'green' : tg?.enabled ? 'amber' : 'slate'}>{isLoading ? 'Checking…' : live ? 'Live' : tg?.enabled ? 'Needs connecting' : 'Not set up'}</Badge></summary>
    <div className="connection-body">
      <p>Customers message your agent on Telegram. To keep orders private, the bot first asks for the <strong>order ID and the name on the order</strong> (e.g. <code>LL1042 Priya Sharma</code>), then the agent helps with that order using your knowledge, rules and live order data.</p>
      {error ? <p role="alert" className="text-sm text-red-600">{(error as Error).message}</p> : !tg?.enabled ? (
        <p className="text-sm text-slate-600">Add <code>TELEGRAM_BOT_TOKEN</code> (from @BotFather) to the server's settings to turn this on.</p>
      ) : <>
        <div className="connection-copy-row"><code>{tg.link}</code>
          <a className="dashboard-icon-button" href={tg.link} target="_blank" rel="noreferrer" aria-label="Open the bot in Telegram"><ExternalLink size={16} /></a>
          <button type="button" className="dashboard-icon-button" aria-label="Copy bot link" onClick={() => navigator.clipboard.writeText(tg.link)}><Copy size={16} /></button></div>
        {!live && <p className="text-sm text-slate-600">{tg.webhook_url ? 'The bot currently sends messages to a different server.' : 'The bot is not pointed at this server yet.'} Connect it so messages reach this dashboard.</p>}
        {tg.last_error && <p className="text-sm text-amber-700">Telegram reported: {tg.last_error}</p>}
        <Button size="sm" variant={live ? 'secondary' : 'primary'} onClick={() => connect.mutate()} loading={connect.isPending}>{live ? 'Reconnect bot' : 'Connect bot to this server'}</Button>
        {connect.error && <p role="alert" className="text-sm text-red-600">{(connect.error as Error).message}</p>}
      </>}
      <p className="text-[11px] text-slate-500">WhatsApp is paused until Meta verifies the business; replies and proactive updates go to Telegram meanwhile.</p>
    </div>
  </details>
}

function SaveState({ saved, error }: { saved: boolean; error: Error | null }) {
  if (error) return <p role="alert" className="text-sm text-red-600">{error.message}</p>
  return saved ? <p role="status" className="text-sm text-emerald-700">Connection details saved.</p> : null
}

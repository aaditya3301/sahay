import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ExternalLink } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { Badge, Button, Field, inputCls } from './ui'

export function EmailPanel({ integrations, open = false }: { integrations: any[]; platformEmail?: boolean; open?: boolean }) {
  const qc = useQueryClient()
  const config = (kind: string) => integrations.find((item: any) => item.kind === kind)?.config || {}
  const [em, setEm] = useState({ inbound_address: config('email').inbound_address || '', from: config('email').from || '' })
  const save = useMutation({
    mutationFn: (body: typeof em) => api('/v1/integrations/email', { method: 'PUT', body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['integration-health'] }) },
  })
  // The addresses are only kept on file, so save them as the user goes: when a field loses focus and when the step is left.
  const latest = useRef(em)
  latest.current = em
  const lastSaved = useRef(JSON.stringify(em))
  const persist = () => {
    const now = JSON.stringify(latest.current)
    if (now === lastSaved.current) return
    lastSaved.current = now
    const cfg = { ...latest.current }
    qc.setQueryData(['settings'], (old: any) => old && { ...old, integrations: [...(old.integrations || []).filter((i: any) => i.kind !== 'email'), { kind: 'email', config: cfg, status: 'active' }] })
    api('/v1/integrations/email', { method: 'PUT', body: cfg }).then(() => qc.invalidateQueries({ queryKey: ['settings'] })).catch(() => { lastSaved.current = '' })
  }
  useEffect(() => () => persist(), [])
  return (
    <details className="connection-disclosure" open={open}>
      <summary><span>Email</span><Badge tone="green">Ready</Badge></summary>
      <div className="connection-body">
        <p>Add the email addresses you want customers to use. For now Sahay sends and receives your email for you, so these are saved for later.</p>
        <div className="setup-fields">
          <Field label="Receiving email address"><input className={inputCls} type="email" placeholder="Enter email address" autoComplete="off" value={em.inbound_address} onBlur={persist} onChange={(e) => setEm({ ...em, inbound_address: e.target.value })} /></Field>
          <Field label="Sending email address"><input className={inputCls} type="email" placeholder="Enter email address" autoComplete="off" value={em.from} onBlur={persist} onChange={(e) => setEm({ ...em, from: e.target.value })} /></Field>
        </div>
        <Button size="sm" variant="secondary" onClick={() => { lastSaved.current = JSON.stringify(em); save.mutate(em) }} loading={save.isPending}>Save email addresses</Button>
        <SaveState saved={save.isSuccess} error={save.error} />
      </div>
    </details>
  )
}

// Customers reach the agent on Telegram (@ the platform bot). They verify with order ID + the name on the order.
export function TelegramPanel({ open = true }: { open?: boolean }) {
  const { data: tg, isLoading, error } = useQuery({ queryKey: ['telegram'], queryFn: () => api('/v1/telegram'), retry: false })
  return <details className="connection-disclosure" open={open}>
    <summary><span>Telegram</span><Badge tone={tg?.enabled ? 'green' : 'slate'}>{isLoading ? 'Checking…' : tg?.enabled ? 'Agent bot configured' : 'Not set up'}</Badge></summary>
    <div className="connection-body">
      {isLoading ? null : error ? <p role="alert" className="text-sm text-red-600">{(error as Error).message}</p> : !tg?.enabled ? (
        <p className="text-sm text-slate-600">The Telegram bot isn't set up on this server yet.</p>
      ) : <>
        <p>Your agent is ready on Telegram. Customers can open the bot below and ask anything: where their order is, questions about your products, returns or replacements.</p>
        <div className="connection-copy-row"><code>{tg.link}</code>
          <a className="dashboard-icon-button" href={tg.link} target="_blank" rel="noreferrer" aria-label="Open the bot in Telegram"><ExternalLink size={16} /></a>
          <button type="button" className="dashboard-icon-button" aria-label="Copy bot link" onClick={() => navigator.clipboard.writeText(tg.link)}><Copy size={16} /></button></div>
        <p className="text-sm text-slate-600">To keep orders private, the bot first asks for the order ID and the name on the order. Share this link on your website, packaging or social pages so customers can find it.</p>
      </>}
    </div>
  </details>
}

function SaveState({ saved, error }: { saved: boolean; error: Error | null }) {
  if (error) return <p role="alert" className="text-sm text-red-600">{error.message}</p>
  return saved ? <p role="status" className="text-sm text-emerald-700">Connection details saved.</p> : null
}

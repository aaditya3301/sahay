import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Megaphone, Newspaper, Play, Plus, ShieldCheck, ShoppingCart } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, ChannelIcon, Empty, Field, inputCls, Modal, PageHeader, Spinner, Tabs } from '../components/ui'
import { api } from '../lib/api'
import { ago, humanize, time } from '../lib/format'

const statusTone = (s: string) => ({ draft: 'amber', approved: 'blue', sending: 'blue', sent: 'green', rejected: 'slate' } as any)[s] || 'slate'

export default function Campaigns() {
  const [tab, setTab] = useState('campaigns')
  return (
    <>
      <PageHeader title="Campaigns" subtitle="Campaigns, follow-ups and automations." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'campaigns', label: 'Campaigns' }, { id: 'auto', label: 'Automations' }]} />
      <div className="mt-4">{tab === 'campaigns' ? <CampaignList /> : <Automations />}</div>
    </>
  )
}

function CampaignList() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['campaigns'], queryFn: () => api('/v1/campaigns'), refetchInterval: 4000 })
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const [f, setF] = useState({ name: '', goal: '', channel: 'telegram', segment: 'recent_buyers' })
  const newsletter = useMutation({
    mutationFn: () => api('/v1/automations/newsletter', { method: 'POST' }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['campaigns'] }); if (c?.id) setSel(c.id) },
  })
  const create = useMutation({
    mutationFn: () => api('/v1/campaigns', { body: { name: f.name, goal: f.goal, channel: f.channel, audience: { segment: f.segment } } }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['campaigns'] }); setOpen(false); setSel(c.id); setF({ ...f, name: '', goal: '' }) },
  })
  if (isLoading) return <Spinner />
  return (
    <>
      <div className="campaign-toolbar">
        <div className="campaign-approval-note" title="Customers with an open angry ticket are never sent promotional messages."><ShieldCheck size={16} />You approve every campaign before it sends.</div>
        <div className="campaign-toolbar-actions">
          <Button variant="secondary" onClick={() => newsletter.mutate()} loading={newsletter.isPending} title="Draft this month's newsletter"><Newspaper className="h-4 w-4" />Draft newsletter</Button>
          <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />New campaign</Button>
        </div>
      </div>
      <Card pad={false}>
        {!data?.campaigns?.length ? <Empty icon={<Megaphone className="h-10 w-10" />} title="No campaigns yet">Create a campaign to draft a message for your customers.</Empty> : (
          <div className="divide-y divide-slate-100">
            {data.campaigns.map((c: any) => (
              <button key={c.id} onClick={() => setSel(c.id)} className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-50">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-medium"><ChannelIcon channel={c.channel} />{c.name}{c.kind === 'newsletter' && <Badge tone="violet">newsletter</Badge>}</div>
                  {c.subject && <div className="truncate text-xs text-slate-600">Subject: {c.subject}</div>}
                  <div className="truncate text-xs text-slate-500">{c.goal} · {humanize(c.audience?.segment)}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
                  {c.status === 'sent' && <span>{(c.stats?.sent || 0) + (c.stats?.simulated || 0)} sent · {c.stats?.suppressed || 0} held back</span>}
                  <Badge tone={statusTone(c.status)}>{c.status}</Badge><span>{ago(c.created_at)}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </Card>
      <Modal open={open} onClose={() => setOpen(false)} title="New campaign">
        <div className="space-y-3">
          <Field label="Name"><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Diwali gifting" /></Field>
          <Field label="Goal" hint="The AI writes the message from this, using only products in stock and real prices."><textarea className={inputCls} rows={3} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} placeholder="Remind recent buyers about our gift boxes for Diwali; free gift wrap this week." /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Channel"><select className={inputCls} value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}><option value="telegram">Telegram</option><option value="email">Email</option></select></Field>
            <Field label="Audience"><select className={inputCls} value={f.segment} onChange={(e) => setF({ ...f, segment: e.target.value })}>{(data?.segments || []).map((s: any) => <option key={s.id} value={s.id}>{s.label}</option>)}</select></Field>
          </div>
          {create.isError && <div className="text-xs text-red-600">{(create.error as Error).message}</div>}
          <div className="flex justify-end"><Button onClick={() => create.mutate()} loading={create.isPending} disabled={!f.name || !f.goal}>{create.isPending ? 'AI is drafting…' : 'Create draft'}</Button></div>
        </div>
      </Modal>
      {sel && <CampaignDetail id={sel} onClose={() => setSel(null)} />}
    </>
  )
}

function CampaignDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['campaign', id], queryFn: () => api(`/v1/campaigns/${id}`), refetchInterval: (q) => (['approved', 'sending'].includes(q.state.data?.campaign?.status) ? 2000 : false) })
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => { setMsg(null) }, [data?.campaign?.message])
  const inv = () => { qc.invalidateQueries({ queryKey: ['campaign', id] }); qc.invalidateQueries({ queryKey: ['campaigns'] }) }
  const save = useMutation({ mutationFn: () => api(`/v1/campaigns/${id}`, { method: 'PATCH', body: { message: msg } }), onSuccess: inv })
  const redraft = useMutation({ mutationFn: () => api(`/v1/campaigns/${id}/redraft`, { method: 'POST' }), onSuccess: inv })
  const approve = useMutation({ mutationFn: async () => { if (msg !== null) await api(`/v1/campaigns/${id}`, { method: 'PATCH', body: { message: msg } }); return api(`/v1/campaigns/${id}/approve`, { method: 'POST' }) }, onSuccess: inv })
  const reject = useMutation({ mutationFn: () => api(`/v1/campaigns/${id}/reject`, { method: 'POST' }), onSuccess: inv })
  const c = data?.campaign
  const pv = data?.audience_preview
  return (
    <Modal open onClose={onClose} title={c ? c.name : 'Campaign'} wide>
      {!c ? <Spinner /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm"><Badge tone={statusTone(c.status)}>{c.status}</Badge><ChannelIcon channel={c.channel} />{humanize(c.channel)} · {humanize(c.audience?.segment)}<span className="text-slate-500">· {c.goal}</span></div>
          <Field label="Message" hint="{first_name} is replaced for each customer.">
            <textarea className={inputCls} rows={7} value={msg ?? c.message} disabled={c.status !== 'draft'} onChange={(e) => setMsg(e.target.value)} />
          </Field>
          {c.status === 'draft' && (
            <div className="flex flex-wrap gap-2">
              {msg !== null && msg !== c.message && <Button variant="secondary" onClick={() => save.mutate()} loading={save.isPending}>Save edit</Button>}
              <Button variant="secondary" onClick={() => redraft.mutate()} loading={redraft.isPending}>Redraft with AI</Button>
            </div>
          )}
          {pv && (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-slate-200 p-3">
                <div className="text-xs font-medium text-slate-500">Will receive</div>
                <div className="text-2xl font-semibold tabular-nums">{pv.eligible}</div>
                <div className="mt-1 text-xs text-slate-500">{pv.sample.join(', ')}{pv.eligible > pv.sample.length ? '…' : ''}</div>
              </div>
              <div className="rounded-lg border border-red-100 bg-red-50/50 p-3">
                <div className="text-xs font-medium text-red-700">Held back: open angry ticket ({pv.suppressed.length})</div>
                <div className="mt-1 max-h-28 space-y-0.5 overflow-y-auto text-xs text-slate-600 scroll-thin">
                  {pv.suppressed.length ? pv.suppressed.map((s: any, i: number) => <div key={i}>{s.name} — {s.reason}</div>) : <span className="text-slate-400">Nobody — no unhappy customers in this audience.</span>}
                </div>
              </div>
            </div>
          )}
          {c.status === 'draft' && (
            <div className="flex items-center gap-2 border-t pt-3">
              <Button variant="success" onClick={() => approve.mutate()} loading={approve.isPending}>Approve & send{pv ? ` to ${pv.eligible}` : ''}</Button>
              <Button variant="ghost" onClick={() => reject.mutate()} loading={reject.isPending}>Reject</Button>
              <span className="text-[11px] text-slate-400">Recorded as simulated until Telegram / email are connected.</span>
            </div>
          )}
          {['approved', 'sending'].includes(c.status) && <div className="rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-900">Sending…</div>}
          {c.status === 'sent' && (
            <div className="grid grid-cols-4 gap-2 text-center text-sm">
              {['sent', 'simulated', 'failed', 'suppressed'].map((k) => <div key={k} className="rounded-lg bg-slate-50 p-2"><div className="text-lg font-semibold tabular-nums">{c.stats?.[k] || 0}</div><div className="text-xs text-slate-500">{k === 'suppressed' ? 'held back' : k}</div></div>)}
            </div>
          )}
          {data.sends?.length > 0 && (
            <div className="max-h-56 overflow-y-auto rounded-lg border border-slate-100 scroll-thin">
              <table className="w-full text-xs"><tbody className="divide-y divide-slate-100">
                {data.sends.map((s: any, i: number) => <tr key={i}><td className="px-3 py-1.5">{s.display_name || s.skip_reason}</td><td><Badge tone={s.status === 'skipped' ? 'red' : s.status === 'failed' ? 'red' : 'green'}>{s.status}</Badge></td><td className="text-slate-400">{time(s.created_at)}</td></tr>)}
              </tbody></table>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

const kindLabel = (k: string) => ({ delay_alert: 'Delay alert', cod_confirm: 'COD confirmation', cart_reminder: 'Cart reminder', newsletter: 'Newsletter' } as any)[k] || humanize(k)
const kindTone = (k: string) => ({ delay_alert: 'orange', cod_confirm: 'blue', cart_reminder: 'violet', newsletter: 'green' } as any)[k] || 'slate'

function WaBubble({ text, when }: { text: string; when?: string }) {
  return (
    <div className="automation-message px-3 py-2 text-sm text-slate-800">
      <div className="whitespace-pre-wrap">{text}</div>
      {when && <div className="mt-1 text-right text-xs text-slate-500">{time(when)}</div>}
    </div>
  )
}

function Automations() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['automations'], queryFn: () => api('/v1/automations'), refetchInterval: 5000 })
  const run = useMutation({ mutationFn: () => api('/v1/automations/run', { method: 'POST' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['automations'] }) })
  const summary = useMutation({ mutationFn: () => api('/v1/automations/daily-summary', { method: 'POST' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['automations'] }) })
  const { data: carts } = useQuery({ queryKey: ['carts'], queryFn: () => api('/v1/automations/carts'), refetchInterval: 8000 })
  if (isLoading) return <Spinner />
  return (
    <div className="automations-layout">
      <div className="space-y-4">
        <Card title="Customer follow-ups" action={<Button size="sm" onClick={() => run.mutate()} loading={run.isPending}><Play className="h-3.5 w-3.5" />Check now</Button>}>
          <dl className="automation-types">
            {[
              ['Delivery delays', 'Let customers know when a parcel is overdue.'],
              ['Cash-on-delivery orders', 'Ask customers to confirm before shipping.'],
              ['Lead follow-ups', 'Draft a follow-up for your approval.'],
              ['Callbacks', 'Return calls booked by your customers.'],
              ['Cart reminders', 'Remind shoppers about unfinished checkouts.'],
              ['Monthly newsletter', 'Draft an email for you to review.'],
            ].map(([t, d]) => <div key={t}><dt>{t}</dt><dd>{d}</dd></div>)}
          </dl>
          <details className="dashboard-disclosure automation-explanation"><summary>Timing and customer safeguards</summary><div className="setup-help space-y-2"><p>When scheduling is configured, checks run at 9am, 1pm, 5pm and 9pm IST. The evening run also sends your daily summary.</p><p>Cart reminders are sent once and skipped if the customer ordered, opted out or is upset. Lead follow-ups and newsletters are drafted for approval. Newsletters include an unsubscribe link.</p><p>Cash-on-delivery confirmations use Telegram, plus a call if enabled. Callbacks run when due. Follow-ups stop after the last scheduled attempt.</p></div></details>
          {run.data && <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(run.data).map(([k, v]) => <Badge key={k} tone={(v as number) > 0 ? 'green' : 'slate'}>{humanize(k)}: {String(v)}</Badge>)}</div>}
          {run.isError && <div className="mt-2 text-xs text-red-600">{(run.error as Error).message}</div>}
        </Card>
        <Card title="Recent activity" pad={false} className="automation-history">
          {!data?.events?.length ? <Empty title="No messages sent yet">Customer follow-ups will appear here.</Empty> : (
            <div className="divide-y divide-slate-100">
              {data.events.map((e: any) => (
                <div key={e.id} className="px-4 py-3">
                  <div className="flex items-center gap-2 text-sm"><Badge tone={kindTone(e.kind)}>{kindLabel(e.kind)}</Badge><span className="font-medium">{e.display_name || '—'}</span><span className="text-slate-500">{e.summary}</span>
                    <span className="ml-auto text-xs text-slate-400">{ago(e.created_at)}</span>{e.data?.status && <Badge>{e.data.status}</Badge>}</div>
                  {e.data?.skipped && <div className="mt-1 text-xs text-slate-500">Not sent: {e.data.skipped}</div>}
                  {e.data?.text && <div className="mt-1.5 max-w-xl"><WaBubble text={e.data.text} /></div>}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <div className="space-y-4">
        <Card title="Daily summary">
          <div className="automation-summary">
            {summary.data?.text ? <WaBubble text={summary.data.text} /> : data?.last_summary ? <WaBubble text={data.last_summary.data?.text} when={data.last_summary.created_at} /> : <div className="text-center text-xs text-slate-500">No summary yet.</div>}
          </div>
          <p className="setup-help">Sent to the owner’s phone number. Demo mode until a messaging channel is connected.</p>
          <Button size="sm" variant="secondary" className="mt-3" onClick={() => summary.mutate()} loading={summary.isPending}>Send today’s summary</Button>
        </Card>
        <Card title="Carts" action={<ShoppingCart className="h-4 w-4 text-slate-400" />}>
          {!carts?.length ? <div className="text-xs text-slate-500">No carts yet. Add items on the demo storefront and leave checkout.</div> : (
            <div className="space-y-1.5 text-xs">
              {carts.slice(0, 12).map((c: any) => (
                <div key={c.id} className="flex items-center gap-2">
                  <span className="truncate font-medium">{c.customer_name || c.customer_phone || c.customer_email || 'Visitor'}</span>
                  <span className="text-slate-500">{c.items?.length} item{c.items?.length === 1 ? '' : 's'} · ₹{(c.total_paise / 100).toLocaleString('en-IN')}</span>
                  <span className="ml-auto"><Badge tone={({ open: 'amber', reminded: 'blue', converted: 'green', expired: 'slate' } as any)[c.status]}>{c.status}</Badge></span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Automation preferences">
          <dl className="automation-preferences">
            <div><dt>Save-the-order offers</dt><dd>{data?.settings?.save_the_order ? 'On' : 'Off'}</dd></div>
            <div><dt>Confirmation calls</dt><dd>{data?.settings?.cod_calls ? 'On' : 'Off'}</dd></div>
            <div><dt>Lead follow-up</dt><dd>After {data?.settings?.follow_up_days ?? 3} days</dd></div>
            <div><dt>Cart reminder</dt><dd>{data?.settings?.cart_reminders === false ? 'Off' : `After ${data?.settings?.cart_reminder_hours ?? 1}h`}</dd></div>
            <div><dt>Newsletter draft</dt><dd>{data?.settings?.newsletter === false ? 'Off' : 'On'}</dd></div>
          </dl>
          <a href="/integrations?tab=agent" className="setup-text-link">Edit agent settings →</a>
        </Card>
        {data?.runs?.length > 0 && (
          <Card title="Recent runs">
            <div className="space-y-1 text-xs">{data.runs.map((r: any, i: number) => <div key={i} className="flex justify-between"><span className="text-slate-500">{time(r.created_at)}</span><span>{Object.entries(r.data || {}).filter(([, v]) => (v as number) > 0).map(([k, v]) => `${humanize(k)} ${v}`).join(' · ') || 'nothing due'}</span></div>)}</div>
          </Card>
        )}
      </div>
    </div>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail, Megaphone, Newspaper, Plus, ShieldCheck, Target, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, ChannelIcon, Empty, Field, inputCls, Modal, PageHeader, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { ago, humanize, time } from '../lib/format'

const statusTone = (s: string) => ({ draft: 'amber', approved: 'blue', sending: 'blue', sent: 'green', rejected: 'slate' } as any)[s] || 'slate'

export default function Campaigns() {
  return (
    <>
      <PageHeader title="Campaigns" subtitle="Bulk emails to all your customers: an occasion, a launch or an offer." />
      <CampaignList />
    </>
  )
}

function CampaignList() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['campaigns'], queryFn: () => api('/v1/campaigns'), refetchInterval: 4000 })
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const [f, setF] = useState({ name: '', goal: '' })
  const newsletter = useMutation({
    mutationFn: () => api('/v1/automations/newsletter', { method: 'POST' }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['campaigns'] }); if (c?.id) setSel(c.id) },
  })
  const create = useMutation({
    mutationFn: () => api('/v1/campaigns', { body: { name: f.name, goal: f.goal } }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ['campaigns'] }); setOpen(false); setSel(c.id); setF({ name: '', goal: '' }) },
  })
  if (isLoading) return <Spinner />
  const reach = data?.reach || {}
  return (
    <>
      <div className="campaign-groups">
        <div className="campaign-group is-on"><Users size={18} aria-hidden="true" /><div><strong>{reach.customers ?? 0} {reach.customers === 1 ? 'customer' : 'customers'}</strong><span>People who have ordered. Campaigns are emailed to all of them.</span></div></div>
        <div className="campaign-group"><Target size={18} aria-hidden="true" /><div><strong>{reach.leads ?? 0} sales {reach.leads === 1 ? 'lead' : 'leads'}</strong><span>Not customers yet. Never in a campaign: you reach them one by one from Leads.</span></div></div>
      </div>
      <div className="campaign-toolbar">
        <div className="campaign-approval-note" title="Customers with an open angry ticket are never sent promotional messages."><ShieldCheck size={16} />You approve every campaign before it sends.</div>
        <div className="campaign-toolbar-actions">
          <Button variant="secondary" onClick={() => newsletter.mutate()} loading={newsletter.isPending} title="Draft this month's newsletter"><Newspaper className="h-4 w-4" />Draft newsletter</Button>
          <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />New campaign</Button>
        </div>
      </div>
      <Card pad={false}>
        {!data?.campaigns?.length ? <Empty icon={<Megaphone className="h-10 w-10" />} title="No campaigns yet">Create a campaign to email all your customers about an occasion, a launch or an offer.</Empty> : (
          <div className="divide-y divide-slate-100">
            {data.campaigns.map((c: any) => (
              <button key={c.id} onClick={() => setSel(c.id)} className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-50">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-medium"><ChannelIcon channel={c.channel} />{c.name}{c.kind === 'newsletter' && <Badge tone="violet">newsletter</Badge>}</div>
                  {c.subject && <div className="truncate text-xs text-slate-600">Subject: {c.subject}</div>}
                  <div className="truncate text-xs text-slate-500">{c.goal} · {c.channel === 'email' && c.audience?.segment === 'all' ? 'All customers' : humanize(c.audience?.segment)}</div>
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
          <Field label="What's it about?" hint="The AI writes the email from this, using only products in stock and real prices."><textarea className={inputCls} rows={3} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} placeholder="Diwali is coming: our gift boxes, with free gift wrap this week." /></Field>
          <p className="campaign-audience-note"><Mail size={15} aria-hidden="true" />{reach.customers === 1 ? 'Emailed to your 1 customer who has ordered.' : `Emailed to all ${reach.customers ?? 0} customers who have ordered.`} Sales leads are never included.</p>
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
          <div className="flex flex-wrap items-center gap-2 text-sm"><Badge tone={statusTone(c.status)}>{c.status}</Badge><ChannelIcon channel={c.channel} />{humanize(c.channel)} · {c.audience?.segment === 'all' ? 'All customers' : humanize(c.audience?.segment)}<span className="text-slate-500">· {c.goal}</span></div>
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
              <span className="text-[11px] text-slate-400">Recorded as simulated until email sending is set up.</span>
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

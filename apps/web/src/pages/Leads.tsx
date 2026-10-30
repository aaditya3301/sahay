import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Copy, Mail, Phone, Plus, Search, Target, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card, ChannelIcon, cx, Empty, Field, inputCls, Modal, PageHeader, Spinner, Tabs } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize, time } from '../lib/format'

export const STAGES = ['new', 'contacted', 'engaged', 'qualified', 'callback', 'customer', 'dropped', 'needs_attention']
const GROUPS: [string, string][] = [['all', 'All'], ['new', 'New'], ['in_progress', 'In progress'], ['callback', 'Callback later'], ['customer', 'Customers'], ['dropped', 'Dropped'], ['needs_attention', 'Needs attention']]

export const stageTone = (s?: string) => ({ new: 'blue', contacted: 'slate', engaged: 'violet', qualified: 'brand', callback: 'amber', customer: 'green', dropped: 'slate', needs_attention: 'red' } as any)[s || ''] || 'slate'
const sourceLabel = (s?: string) => ({ webchat: 'Website chat', outbound: 'Lead search', import: 'CSV import', manual: 'Added by team', whatsapp: 'WhatsApp', email: 'Email', voice: 'Phone call' } as any)[s || ''] || humanize(s)
const emailTone = (s?: string) => ({ valid: 'green', risky: 'amber', invalid: 'red' } as any)[s || ''] || 'slate'

export function ScorePill({ score }: { score?: number | null }) {
  if (score == null) return <span className="text-xs text-slate-400">—</span>
  const c = score >= 70 ? 'bg-emerald-600' : score >= 40 ? 'bg-amber-500' : 'bg-slate-400'
  return <span className={cx('inline-flex h-6 min-w-[2rem] items-center justify-center rounded-full px-1.5 text-xs font-semibold tabular-nums text-white', c)}>{score}</span>
}

export default function Leads() {
  const qc = useQueryClient()
  const [group, setGroup] = useState('all')
  const [q, setQ] = useState('')
  const [modal, setModal] = useState<'' | 'find' | 'add' | 'import'>('')
  const { data, isLoading } = useQuery({ queryKey: ['leads', group, q], queryFn: () => api(`/v1/leads?group=${group}&q=${encodeURIComponent(q)}`), refetchInterval: 4000 })
  const refresh = () => qc.invalidateQueries({ queryKey: ['leads'] })
  const running = (data?.searches || []).filter((s: any) => s.status === 'running')
  return (
    <>
      <PageHeader title="Leads" subtitle="All your prospects in one pipeline."
        actions={<>
          <input className={inputCls + ' w-56'} placeholder="Search leads" value={q} onChange={(e) => setQ(e.target.value)} />
          <Button variant="secondary" onClick={() => setModal('import')}><Upload className="h-4 w-4" />Import CSV</Button>
          <Button variant="secondary" onClick={() => setModal('add')}><Plus className="h-4 w-4" />Add lead</Button>
          <Button onClick={() => setModal('find')}><Search className="h-4 w-4" />Find leads</Button>
        </>} />
      {data?.drafts_pending > 0 && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <Mail className="h-4 w-4" />{data.drafts_pending} email draft{data.drafts_pending > 1 ? 's are' : ' is'} waiting for your approval. Nothing is sent until you approve.
          <button className="ml-auto text-xs font-medium underline" onClick={() => setGroup('all')}>Leads with drafts are marked ✉</button>
        </div>
      )}
      {running.length > 0 && (
        <div className="mb-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          🔎 Searching the web for “{running[0].query}”… leads appear here as they're found, verified and scored.
        </div>
      )}
      <Tabs value={group} onChange={setGroup} tabs={GROUPS.map(([id, label]) => ({ id, label: <>{label} <span className="ml-0.5 text-xs text-slate-400">{data?.groups?.[id] ?? ''}</span></> }))} />
      <Card pad={false} className="mt-3">
        {isLoading ? <Spinner /> : !data?.leads?.length ? (
          <Empty icon={<Target className="h-10 w-10" />} title="No leads here yet">Leads arrive when website visitors show buying interest, when you run a lead search, or import a CSV.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-4 py-2">Lead</th><th>Score</th><th>Stage</th><th>Source</th><th>Email</th><th>Interest</th><th>Last contact</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.leads.map((l: any) => (
                <tr key={l.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2.5">
                    <Link to={`/leads/${l.id}`} className="font-medium text-brand-700 hover:underline">{l.name || l.company || l.email || l.phone || 'Unnamed'}</Link>
                    {l.drafts > 0 && <Badge tone="amber" className="ml-1">✉ draft</Badge>}
                    <div className="text-xs text-slate-500">{[l.name && l.company, l.city].filter(Boolean).join(' · ')}</div>
                  </td>
                  <td><ScorePill score={l.score} /></td>
                  <td><Badge tone={stageTone(l.stage)}>{humanize(l.stage)}</Badge></td>
                  <td className="text-xs text-slate-600">{sourceLabel(l.source)}</td>
                  <td>{l.email_status ? <Badge tone={emailTone(l.email_status)}>{l.email_status}</Badge> : <span className="text-xs text-slate-400">{l.email ? '…' : '—'}</span>}</td>
                  <td className="max-w-[260px] truncate text-xs text-slate-600" title={l.interest}>{l.interest || '—'}</td>
                  <td className="text-xs text-slate-500">{ago(l.last_contact_at || l.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <FindModal open={modal === 'find'} onClose={() => setModal('')} searches={data?.searches || []} onDone={refresh} />
      <AddModal open={modal === 'add'} onClose={() => setModal('')} onDone={refresh} />
      <ImportModal open={modal === 'import'} onClose={() => setModal('')} onDone={refresh} />
    </>
  )
}

function FindModal({ open, onClose, searches, onDone }: { open: boolean; onClose: () => void; searches: any[]; onDone: () => void }) {
  const [query, setQuery] = useState('')
  const [count, setCount] = useState(8)
  const [sid, setSid] = useState<string | null>(null)
  const start = useMutation({ mutationFn: () => api('/v1/leads/search', { body: { query, count } }), onSuccess: (r) => { setSid(r.search.id); onDone() } })
  const { data: status } = useQuery({ queryKey: ['lead-search', sid], queryFn: () => api(`/v1/leads/searches/${sid}`), enabled: !!sid, refetchInterval: (q) => (q.state.data?.status === 'running' ? 3000 : false) })
  useEffect(() => { if (status && status.status !== 'running') onDone() }, [status?.status])
  return (
    <Modal open={open} onClose={onClose} title="Find leads on the web">
      <div className="space-y-3">
        <p className="text-sm text-slate-500">Describe who you want to sell to. The AI searches Google, then verifies emails, scores each lead against your ideal customer and drafts a first email for you to approve.</p>
        <Field label="Who are you looking for?"><textarea className={inputCls} rows={3} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. Corporate gifting companies in Bengaluru, or boutique gift shops in Pune" /></Field>
        <Field label="How many"><input type="number" min={1} max={20} className={inputCls + ' w-24'} value={count} onChange={(e) => setCount(Number(e.target.value))} /></Field>
        <Button onClick={() => start.mutate()} loading={start.isPending} disabled={query.trim().length < 4}><Search className="h-4 w-4" />Start search</Button>
        {start.isError && <div className="text-xs text-red-600">{(start.error as Error).message}</div>}
        {status && (
          <div className={cx('rounded-lg px-3 py-2 text-sm', status.status === 'running' ? 'bg-blue-50 text-blue-900' : status.status === 'failed' ? 'bg-red-50 text-red-800' : 'bg-emerald-50 text-emerald-900')}>
            {status.status === 'running' ? 'Accepted — searching in the background. You can close this.' : status.status === 'failed' ? `Search failed: ${status.error}` : `Done: ${status.found} new leads added. Scoring and email drafts follow in a minute.`}
          </div>
        )}
        {searches.length > 0 && (
          <div className="border-t pt-3">
            <div className="mb-1 text-xs font-medium text-slate-500">Recent searches</div>
            {searches.map((s) => <div key={s.id} className="flex justify-between py-0.5 text-xs"><span className="truncate text-slate-700">{s.query}</span><span className="ml-2 whitespace-nowrap text-slate-400">{s.status === 'done' ? `${s.found} found` : s.status} · {ago(s.created_at)}</span></div>)}
          </div>
        )}
      </div>
    </Modal>
  )
}

function AddModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const blank = { name: '', company: '', email: '', phone: '', website: '', city: '', interest: '', notes: '' }
  const [f, setF] = useState<any>(blank)
  const add = useMutation({ mutationFn: () => api('/v1/leads', { body: Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v || null])) }), onSuccess: () => { setF(blank); onDone(); onClose() } })
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  return (
    <Modal open={open} onClose={onClose} title="Add a lead">
      <div className="grid grid-cols-2 gap-3">
        {['name', 'company', 'email', 'phone', 'website', 'city'].map((k) => <Field key={k} label={humanize(k)}><input className={inputCls} value={f[k]} onChange={set(k)} /></Field>)}
        <div className="col-span-2"><Field label="What are they interested in?"><input className={inputCls} value={f.interest} onChange={set('interest')} /></Field></div>
        <div className="col-span-2"><Field label="Notes"><textarea className={inputCls} rows={2} value={f.notes} onChange={set('notes')} /></Field></div>
      </div>
      {add.isError && <div className="mt-2 text-xs text-red-600">{(add.error as Error).message}</div>}
      <div className="mt-4 flex justify-end"><Button onClick={() => add.mutate()} loading={add.isPending}>Add & qualify</Button></div>
    </Modal>
  )
}

function ImportModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const imp = useMutation({ mutationFn: () => { const fd = new FormData(); fd.append('file', file!); return api('/v1/leads/import', { form: fd }) }, onSuccess: () => onDone() })
  return (
    <Modal open={open} onClose={onClose} title="Import leads from CSV">
      <div className="space-y-3">
        <p className="text-sm text-slate-500">Columns: <code>name, email, phone, company, website, city, interest</code>. Duplicates are merged. Each new lead is verified, scored and gets an email draft.</p>
        <input type="file" accept=".csv" className="text-sm" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <div><Button onClick={() => imp.mutate()} loading={imp.isPending} disabled={!file}><Upload className="h-4 w-4" />Import</Button></div>
        {imp.data && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">Added {imp.data.added} new leads.</div>}
        {imp.isError && <div className="text-xs text-red-600">{(imp.error as Error).message}</div>}
      </div>
    </Modal>
  )
}

const eventIcon = (k: string) => ({ created: '✨', scored: '📊', email_checked: '🔍', email_drafted: '📝', email_sent: '📧', email_failed: '⚠️', email_rejected: '🗑', stage: '➡️', chat: '💬', link_opened: '🔗', callback_booked: '📞', call: '📞', edited: '✏️' } as any)[k] || '•'

export function LeadDetail() {
  const tref = useTicketRef()
  const { id } = useParams()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['lead', id], queryFn: () => api(`/v1/leads/${id}`), refetchInterval: 4000 })
  const [notes, setNotes] = useState<string | null>(null)
  const inv = () => { qc.invalidateQueries({ queryKey: ['lead', id] }); qc.invalidateQueries({ queryKey: ['leads'] }) }
  const patch = useMutation({ mutationFn: (body: any) => api(`/v1/leads/${id}`, { method: 'PATCH', body }), onSuccess: inv })
  const qualify = useMutation({ mutationFn: () => api(`/v1/leads/${id}/qualify`, { method: 'POST' }), onSuccess: inv })
  const draft = useMutation({ mutationFn: (kind: string) => api(`/v1/leads/${id}/draft-email?kind=${kind}`, { method: 'POST' }), onSuccess: inv })
  if (isLoading || !data) return <Spinner />
  const l = data.lead
  const sentAny = data.emails.some((e: any) => ['sent', 'simulated'].includes(e.status))
  return (
    <>
      <Link to="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" />Leads</Link>
      <PageHeader title={l.name || l.company || 'Lead'} subtitle={[l.name && l.company, l.city, sourceLabel(l.source), `added ${ago(l.created_at)}`].filter(Boolean).join(' · ')}
        actions={<>
          <select className={inputCls + ' w-44'} value={l.stage} onChange={(e) => patch.mutate({ stage: e.target.value })}>{STAGES.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}</select>
          <Button variant="secondary" onClick={() => qualify.mutate()} loading={qualify.isPending}>Re-qualify</Button>
          {l.email && <Button variant="secondary" onClick={() => draft.mutate(sentAny ? 'followup' : 'first')} loading={draft.isPending}><Mail className="h-4 w-4" />{sentAny ? 'Draft follow-up' : 'Draft email'}</Button>}
        </>} />
      {(qualify.isSuccess || draft.isSuccess) && <div className="mb-3 text-xs text-slate-500">Queued — results appear here in a few seconds.</div>}
      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          {data.emails.filter((e: any) => e.status === 'draft').map((e: any) => <EmailDraft key={e.id} e={e} onDone={inv} />)}
          <Card title="Timeline">
            <div className="space-y-2">
              {data.events.map((e: any) => (
                <div key={e.id} className="flex gap-3 text-sm">
                  <div className="w-28 shrink-0 text-xs text-slate-400">{time(e.created_at)}</div>
                  <div className="w-5 text-center">{eventIcon(e.kind)}</div>
                  <div className="min-w-0 flex-1 text-slate-700">{e.summary}
                    {e.data?.reasons && <ul className="mt-0.5 list-disc pl-4 text-xs text-slate-500">{e.data.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>}
                  </div>
                </div>
              ))}
            </div>
          </Card>
          {data.messages.length > 0 && (
            <Card title="Conversations">
              <div className="space-y-1.5">
                {[...data.messages].reverse().map((m: any, i: number) => (
                  <div key={i} className="flex gap-2 text-sm">
                    <ChannelIcon channel={m.channel} className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span className={m.direction === 'out' ? 'text-brand-700' : 'text-slate-800'}>{m.direction === 'out' ? '↩ ' : ''}{m.text}</span>
                    {m.ticket && <Link to={`/tickets/${m.ticket_id}`} className="ml-auto shrink-0 text-xs text-slate-400 hover:text-brand-700">{tref(m.ticket)}</Link>}
                  </div>
                ))}
              </div>
            </Card>
          )}
          {data.emails.filter((e: any) => e.status !== 'draft').length > 0 && (
            <Card title="Emails">
              <div className="space-y-3">{data.emails.filter((e: any) => e.status !== 'draft').map((e: any) => (
                <div key={e.id} className="rounded-lg border border-slate-100 p-3 text-sm">
                  <div className="mb-1 flex items-center justify-between"><b>{e.subject}</b><Badge tone={e.status === 'sent' ? 'green' : e.status === 'simulated' ? 'blue' : e.status === 'rejected' ? 'slate' : 'red'}>{e.status}</Badge></div>
                  <div className="whitespace-pre-wrap text-xs text-slate-600">{e.body}</div>
                  <div className="mt-1 text-[11px] text-slate-400">{time(e.sent_at || e.created_at)}</div>
                </div>))}</div>
            </Card>
          )}
        </div>
        <div className="space-y-4">
          <Card title="Score">
            <div className="flex items-center gap-3"><ScorePill score={l.score} /><Badge tone={stageTone(l.stage)}>{humanize(l.stage)}</Badge></div>
            {(l.score_reasons || []).length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-slate-600">{l.score_reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>}
            {l.interest && <div className="mt-2 text-sm text-slate-700"><span className="text-xs text-slate-500">Interest: </span>{l.interest}</div>}
            {l.next_action && <div className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-900">Next: {l.next_action}{l.next_action_at ? ` · ${time(l.next_action_at)}` : ''}</div>}
          </Card>
          <Card title="Contact">
            <div className="space-y-1.5 text-sm">
              {l.email && <div className="flex items-center gap-2"><Mail className="h-3.5 w-3.5 text-slate-400" />{l.email}{l.email_status && <Badge tone={emailTone(l.email_status)}>{l.email_status}</Badge>}</div>}
              {l.phone && <div className="flex items-center gap-2"><Phone className="h-3.5 w-3.5 text-slate-400" />{l.phone}</div>}
              {l.website && <a href={l.website.startsWith('http') ? l.website : `https://${l.website}`} target="_blank" className="block text-brand-700 underline">{l.website}</a>}
              {l.customer_id && <Link to={`/customers/${l.customer_id}`} className="block text-xs font-medium text-brand-700 underline">Customer profile →</Link>}
            </div>
          </Card>
          <Card title="Reply Now link">
            <p className="mb-2 text-xs text-slate-500">Signed personal chat link (expires in 30 days). It opens the same AI chat, already knowing who they are and what we emailed.</p>
            <div className="flex gap-2"><input readOnly className={inputCls + ' text-xs'} value={data.reply_url} />
              <Button variant="secondary" size="sm" onClick={() => navigator.clipboard.writeText(data.reply_url)}><Copy className="h-3.5 w-3.5" /></Button></div>
            <a href={data.reply_url} target="_blank" className="mt-1 inline-block text-xs text-brand-700 underline">Open as the lead ↗</a>
          </Card>
          <Card title="Notes">
            <textarea className={inputCls} rows={4} value={notes ?? l.notes ?? ''} onChange={(e) => setNotes(e.target.value)} />
            {notes !== null && notes !== (l.notes ?? '') && <Button size="sm" className="mt-2" onClick={() => patch.mutate({ notes }, { onSuccess: () => setNotes(null) })} loading={patch.isPending}>Save notes</Button>}
          </Card>
          {data.calls.length > 0 && (
            <Card title="Calls">
              <div className="space-y-2">{data.calls.map((c: any) => (
                <Link key={c.id} to={`/calls?id=${c.id}`} className="block rounded-lg border border-slate-100 p-2 text-xs hover:bg-slate-50">
                  <div className="flex justify-between"><span className="font-medium">{humanize(c.direction)} · {humanize(c.purpose)}</span><Badge>{humanize(c.outcome || c.status)}</Badge></div>
                  {c.summary && <div className="mt-0.5 text-slate-600">{c.summary}</div>}
                </Link>))}</div>
            </Card>
          )}
        </div>
      </div>
    </>
  )
}

function EmailDraft({ e, onDone }: { e: any; onDone: () => void }) {
  const [subject, setSubject] = useState(e.subject)
  const [body, setBody] = useState(e.body)
  const approve = useMutation({ mutationFn: () => api(`/v1/outreach/${e.id}/approve`, { body: { subject, body } }), onSuccess: onDone })
  const reject = useMutation({ mutationFn: () => api(`/v1/outreach/${e.id}/reject`, { method: 'POST' }), onSuccess: onDone })
  return (
    <Card title={<span className="flex items-center gap-2"><Mail className="h-4 w-4 text-amber-600" />{e.kind === 'followup' ? 'Follow-up' : 'First'} email — needs your approval</span>} className="border-amber-200">
      <div className="space-y-2">
        <input className={inputCls + ' font-medium'} value={subject} onChange={(x) => setSubject(x.target.value)} />
        <textarea className={inputCls} rows={9} value={body} onChange={(x) => setBody(x.target.value)} />
        <div className="flex items-center gap-2">
          <Button variant="success" onClick={() => approve.mutate()} loading={approve.isPending}>Approve & send</Button>
          <Button variant="ghost" onClick={() => reject.mutate()} loading={reject.isPending}>Reject</Button>
          <span className="text-[11px] text-slate-400">Sent via your email connection; recorded as "simulated" until email (Brevo) is connected.</span>
        </div>
        {approve.isError && <div className="text-xs text-red-600">{(approve.error as Error).message}</div>}
      </div>
    </Card>
  )
}

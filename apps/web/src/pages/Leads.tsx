import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Copy, ExternalLink, Mail, MapPin, Phone, PhoneOutgoing, Plus, Search, Star, Target, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card, ChannelIcon, cx, Empty, Field, inputCls, Modal, PageHeader, Spinner, Tabs } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize, time } from '../lib/format'

export const STAGES = ['new', 'contacted', 'engaged', 'qualified', 'callback', 'customer', 'dropped', 'needs_attention']
const GROUPS: [string, string][] = [['all', 'All'], ['new', 'New'], ['in_progress', 'In progress'], ['callback', 'Callback later'], ['customer', 'Customers'], ['dropped', 'Dropped'], ['needs_attention', 'Needs attention']]

export const stageTone = (s?: string) => ({ new: 'blue', contacted: 'slate', engaged: 'violet', qualified: 'brand', callback: 'amber', customer: 'green', dropped: 'slate', needs_attention: 'red' } as any)[s || ''] || 'slate'
const sourceLabel = (s?: string) => ({ webchat: 'Website chat', outbound: 'Lead search', maps: 'Google Maps', import: 'CSV import', manual: 'Added by team', telegram: 'Telegram', whatsapp: 'Telegram', email: 'Email', voice: 'Phone call' } as any)[s || ''] || humanize(s)
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
  const [modal, setModal] = useState<'' | 'find' | 'maps' | 'add' | 'import'>('')
  const { data, isLoading } = useQuery({ queryKey: ['leads', group, q], queryFn: () => api(`/v1/leads?group=${group}&q=${encodeURIComponent(q)}`), refetchInterval: 4000 })
  const refresh = () => qc.invalidateQueries({ queryKey: ['leads'] })
  const running = (data?.searches || []).filter((s: any) => s.status === 'running')
  return (
    <>
      <PageHeader title="Leads" subtitle="All your prospects in one pipeline." />
      <div className="leads-toolbar">
        <input className={inputCls} placeholder="Search leads" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="leads-actions">
          <Button size="sm" variant="secondary" onClick={() => setModal('import')}><Upload className="h-3.5 w-3.5" />Import CSV</Button>
          <Button size="sm" variant="secondary" onClick={() => setModal('add')}><Plus className="h-3.5 w-3.5" />Add lead</Button>
          <Button size="sm" variant="secondary" onClick={() => setModal('find')}><Search className="h-3.5 w-3.5" />Find leads</Button>
          <Button size="sm" onClick={() => setModal('maps')}><MapPin className="h-3.5 w-3.5" />Scrape Google Maps</Button>
        </div>
      </div>
      {data?.drafts_pending > 0 && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <Mail className="h-4 w-4" />{data.drafts_pending} email draft{data.drafts_pending > 1 ? 's are' : ' is'} waiting for your approval. Nothing is sent until you approve.
          <button className="ml-auto text-xs font-medium underline" onClick={() => setGroup('all')}>View leads with drafts</button>
        </div>
      )}
      {running.length > 0 && (
        <div className="mb-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          {running[0].source === 'maps'
            ? <><MapPin className="mr-1 inline h-4 w-4" aria-hidden="true" />Scraping Google Maps for “{running[0].query}”… the businesses appear here in about a minute.</>
            : <><Search className="mr-1 inline h-4 w-4" aria-hidden="true" />Searching the web for “{running[0].query}”… leads appear here as they're found, verified and scored.</>}
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
                    {l.drafts > 0 && <Badge tone="amber" className="ml-1"><Mail className="h-3 w-3" />Draft</Badge>}
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
      <FindModal open={modal === 'find'} onClose={() => setModal('')} searches={(data?.searches || []).filter((s: any) => s.source !== 'maps')} onDone={refresh} />
      <MapsModal open={modal === 'maps'} onClose={() => setModal('')} onDone={refresh} last={(data?.searches || []).find((s: any) => s.source === 'maps')?.id} />
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

// Google Maps scrape: one query, always 10 businesses, each saved as a lead with its address, phone, rating and Maps link
function MapsModal({ open, onClose, onDone, last }: { open: boolean; onClose: () => void; onDone: () => void; last?: string }) {
  const [query, setQuery] = useState('')
  const [started, setStarted] = useState<string | null>(null)
  const sid = started || last || null  // the scrape just started, else the latest one, so its results are always one click away
  const start = useMutation({ mutationFn: () => api('/v1/leads/scrape-maps', { body: { query } }), onSuccess: (r) => { setStarted(r.search.id); onDone() } })
  const { data: s } = useQuery({ queryKey: ['lead-search', sid], queryFn: () => api(`/v1/leads/searches/${sid}`), enabled: open && !!sid, refetchInterval: (q) => (q.state.data?.status === 'running' ? 2500 : false) })
  useEffect(() => { if (s && s.status !== 'running') onDone() }, [s?.status])
  const running = start.isPending || s?.status === 'running'
  return (
    <Modal open={open} onClose={onClose} title="Scrape Google Maps" wide>
      <div className="space-y-3">
        <p className="text-sm text-slate-500">Say what kind of business and where. Sahay pulls 10 matching businesses from Google Maps with their address, phone, website and rating, and adds each as a lead.</p>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim().length >= 4 && !running) start.mutate() }}>
          <input className={inputCls} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. Bluetooth headband companies in Bangalore" aria-label="What to look for on Google Maps" />
          <Button type="submit" className="shrink-0 whitespace-nowrap" loading={running} disabled={query.trim().length < 4}><MapPin className="h-4 w-4" />{running ? 'Scraping…' : 'Scrape 10'}</Button>
        </form>
        {start.isError && <div className="text-xs text-red-600">{(start.error as Error).message}</div>}
        {running && <div className="rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-900">Scraping Google Maps… this takes about a minute. You can close this; the leads still arrive.</div>}
        {s?.status === 'failed' && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{s.error}</div>}
        {s?.status === 'done' && (
          <>
            <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">“{s.query}”: found {s.results.length} businesses on Google Maps · {s.found} new {s.found === 1 ? 'lead' : 'leads'} added{s.found < s.results.length ? ' (the rest were already in your leads)' : ''}.</div>
            <ol className="maps-results">
              {s.results.map((r: any) => (
                <li key={r.place_id}>
                  <div className="maps-result-head">
                    <Link to={`/leads/${r.lead_id}`} className="font-medium text-brand-700 hover:underline">{r.name}</Link>
                    {r.rating != null && <span className="maps-rating"><Star size={13} aria-hidden="true" />{r.rating}{r.reviews ? ` (${r.reviews})` : ''}</span>}
                    {!r.new && <Badge>Already a lead</Badge>}
                  </div>
                  {r.address && <div className="text-xs text-slate-500">{r.address}</div>}
                  <div className="maps-result-links">
                    {r.phone && <span><Phone size={13} aria-hidden="true" />{r.phone}</span>}
                    {r.website && <a href={r.website.startsWith('http') ? r.website : `https://${r.website}`} target="_blank" rel="noreferrer">{r.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}</a>}
                    <a href={r.maps_url} target="_blank" rel="noreferrer"><ExternalLink size={13} aria-hidden="true" />Google Maps</a>
                  </div>
                </li>
              ))}
            </ol>
          </>
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

const eventLabel = (k: string) => ({ created: 'New lead', scored: 'Scored', email_checked: 'Email check', email_drafted: 'Drafted', email_sent: 'Sent', email_failed: 'Failed', email_rejected: 'Rejected', stage: 'Stage', chat: 'Chat', link_opened: 'Link opened', callback_booked: 'Callback', call: 'Call', edited: 'Edited' } as any)[k] || humanize(k)

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
  const [confirmCall, setConfirmCall] = useState(false)
  const [writeEmail, setWriteEmail] = useState(false)
  const [logCall, setLogCall] = useState(false)
  const call = useMutation({ mutationFn: () => api(`/v1/leads/${id}/call`, { method: 'POST' }), onSuccess: inv })
  if (isLoading || !data) return <Spinner />
  const l = data.lead
  const maps = l.enrichment?.maps_url ? l.enrichment : null
  const sentAny = data.emails.some((e: any) => ['sent', 'simulated'].includes(e.status))
  return (
    <>
      <Link to="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" />Leads</Link>
      <PageHeader title={l.name || l.company || 'Lead'} subtitle={[l.name && l.company, l.city, sourceLabel(l.source), `added ${ago(l.created_at)}`].filter(Boolean).join(' · ')}
        actions={<>
          <select className={inputCls + ' w-44'} value={l.stage} onChange={(e) => patch.mutate({ stage: e.target.value })}>{STAGES.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}</select>
          <Button variant="secondary" onClick={() => qualify.mutate()} loading={qualify.isPending}>Re-qualify</Button>
        </>} />
      {(qualify.isSuccess || draft.isSuccess) && <div className="mb-3 text-xs text-slate-500">Queued — results appear here in a few seconds.</div>}
      <Modal open={confirmCall} onClose={() => setConfirmCall(false)} title={`Call ${l.company || l.name || 'this lead'}?`}>
        <div className="space-y-3 text-sm">
          {!call.data && !call.isError && <>
            <p>Your voice agent calls <b>{l.phone}</b> from your business's phone number, introduces {l.company ? 'you to them' : 'your business'} and finds out what they need. The transcript and summary appear in Sales → Calls and on this lead.</p>
            <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setConfirmCall(false)}>Cancel</Button><Button onClick={() => call.mutate()} loading={call.isPending}><PhoneOutgoing className="h-4 w-4" />Call now</Button></div>
          </>}
          {call.data && (call.data.error
            ? <div className="rounded-lg bg-amber-50 px-3 py-2 text-amber-900">The call is saved in Sales → Calls under “To call” but couldn't be placed: {call.data.error}</div>
            : <div className="rounded-lg bg-emerald-50 px-3 py-2 text-emerald-900">Calling {l.phone} now. Follow it in <Link to={`/calls?id=${call.data.call_id}`} className="underline">Sales → Calls</Link>.</div>)}
          {call.isError && <div className="rounded-lg bg-red-50 px-3 py-2 text-red-800">{(call.error as Error).message}</div>}
        </div>
      </Modal>
      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <Card title="Reach out">
            <div className="reach-grid">
              <button type="button" className="reach-option" disabled={!l.email} onClick={() => setWriteEmail(true)}>
                <Mail size={18} aria-hidden="true" /><span><strong>Write an email</strong><small>{l.email ? 'Your own words, sent from your business email.' : 'No email address on this lead.'}</small></span>
              </button>
              <button type="button" className="reach-option" disabled={!l.phone} onClick={() => setLogCall(true)}>
                <Phone size={18} aria-hidden="true" /><span><strong>Call it yourself</strong><small>{l.phone ? 'Call from your own phone, then log how it went.' : 'No phone number on this lead.'}</small></span>
              </button>
              <button type="button" className="reach-option" disabled={!l.email || draft.isPending} onClick={() => draft.mutate(sentAny ? 'followup' : 'first')}>
                <Mail size={18} aria-hidden="true" /><span><strong>{sentAny ? 'AI follow-up' : 'AI drafts an email'}</strong><small>The AI writes it; nothing sends until you approve.</small></span>
              </button>
              <button type="button" className="reach-option" disabled={!l.phone} onClick={() => { call.reset(); setConfirmCall(true) }}>
                <PhoneOutgoing size={18} aria-hidden="true" /><span><strong>AI calls them</strong><small>Your voice agent calls from your business number.</small></span>
              </button>
            </div>
          </Card>
          <WriteEmailModal open={writeEmail} onClose={() => setWriteEmail(false)} lead={l} onDone={inv} />
          <LogCallModal open={logCall} onClose={() => setLogCall(false)} lead={l} onDone={inv} />
          {data.emails.filter((e: any) => e.status === 'draft').map((e: any) => <EmailDraft key={e.id} e={e} onDone={inv} />)}
          <Card title="Timeline">
            <div className="space-y-2">
              {data.events.map((e: any) => (
                <div key={e.id} className="flex flex-wrap gap-x-3 gap-y-0.5 text-sm">
                  <div className="w-28 shrink-0 text-xs text-slate-400">{time(e.created_at)}</div>
                  <div className="w-24 shrink-0 text-xs font-medium text-slate-500">{eventLabel(e.kind)}</div>
                  <div className="min-w-0 basis-full break-words text-slate-700 sm:basis-0 sm:flex-1">{e.summary}
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
              {maps && <>
                {maps.address && <div className="flex items-start gap-2 text-slate-600"><MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />{maps.address}</div>}
                {maps.rating != null && <div className="flex items-center gap-2 text-slate-600"><Star className="h-3.5 w-3.5 text-amber-500" />{maps.rating}{maps.reviews ? ` · ${maps.reviews} reviews` : ''}</div>}
                <a href={maps.maps_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-medium text-brand-700 underline"><ExternalLink className="h-3 w-3" />View on Google Maps</a>
              </>}
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

// Write your own email to the lead; it goes out through the business's email setup exactly as written
function WriteEmailModal({ open, onClose, lead, onDone }: { open: boolean; onClose: () => void; lead: any; onDone: () => void }) {
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const send = useMutation({ mutationFn: () => api(`/v1/leads/${lead.id}/email`, { body: { subject, body } }), onSuccess: onDone })
  const sentOk = send.data?.status === 'sent' || send.data?.status === 'simulated'
  const close = () => { onClose(); if (sentOk) { setSubject(''); setBody('') } send.reset() }
  return (
    <Modal open={open} onClose={close} title={`Email ${lead.company || lead.name || 'this lead'}`} wide>
      <div className="space-y-3">
        {!sentOk ? <>
          <Field label="To"><input className={inputCls} value={lead.email || ''} readOnly /></Field>
          <Field label="Subject"><input className={inputCls} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="A quick hello from us" /></Field>
          <Field label="Message" hint="Sent exactly as you write it, from your business's email."><textarea className={inputCls} rows={9} value={body} onChange={(e) => setBody(e.target.value)} placeholder={`Hi ${(lead.name || lead.company || '').split(' ')[0] || 'there'},`} /></Field>
          {send.isError && <div className="text-xs text-red-600">{(send.error as Error).message}</div>}
          {send.data && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{send.data.status === 'skipped' ? 'This address can’t receive email, so nothing was sent.' : 'The email couldn’t be sent. Check Workspace → Integrations and try again.'}</div>}
          <div className="flex justify-end gap-2"><Button variant="secondary" onClick={close}>Cancel</Button><Button onClick={() => send.mutate()} loading={send.isPending} disabled={!subject.trim() || !body.trim()}><Mail className="h-4 w-4" />Send email</Button></div>
        </> : (
          <>
            <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{send.data.status === 'sent' ? `Sent to ${lead.email}.` : 'Saved on the lead. It is marked simulated because your email isn’t connected yet.'}</div>
            <div className="flex justify-end"><Button onClick={close}>Done</Button></div>
          </>
        )}
      </div>
    </Modal>
  )
}

const CALL_OUTCOMES: [string, string][] = [['connected', 'Spoke with them'], ['no_answer', 'No answer'], ['voicemail', 'Left a voicemail'], ['call_back', 'Call back later'], ['not_interested', 'Not interested']]

// Call from your own phone, then log it: it shows in Sales → Calls and on the lead, and moves the lead along
function LogCallModal({ open, onClose, lead, onDone }: { open: boolean; onClose: () => void; lead: any; onDone: () => void }) {
  const [outcome, setOutcome] = useState('connected')
  const [notes, setNotes] = useState('')
  const save = useMutation({ mutationFn: () => api(`/v1/leads/${lead.id}/manual-call`, { body: { outcome, notes } }), onSuccess: () => { onDone(); setNotes(''); setOutcome('connected'); onClose() } })
  return (
    <Modal open={open} onClose={onClose} title={`Call ${lead.company || lead.name || 'this lead'} yourself`}>
      <div className="space-y-3 text-sm">
        <a className="reach-dial" href={`tel:${lead.phone}`}><Phone size={16} aria-hidden="true" />{lead.phone}<small>Tap to dial on your phone</small></a>
        <Field label="How did it go?"><select className={inputCls} value={outcome} onChange={(e) => setOutcome(e.target.value)}>{CALL_OUTCOMES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></Field>
        <Field label="Notes" hint="What was said, what they need, what to do next."><textarea className={inputCls} rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        {save.isError && <div className="text-xs text-red-600">{(save.error as Error).message}</div>}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => save.mutate()} loading={save.isPending}>Save call</Button></div>
      </div>
    </Modal>
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

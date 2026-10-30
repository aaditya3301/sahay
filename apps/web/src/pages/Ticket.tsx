import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bot, Brain, CheckCircle2, ChevronDown, ChevronRight, FileText, Image as ImageIcon, Mic, Send, ShieldAlert, ShieldCheck, User, Wrench, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card, ChannelIcon, channelName, cx, FrustrationBar, inputCls, SentimentChip, Spinner, StatusBadge, UrgencyBadge } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api, mediaUrl } from '../lib/api'
import { humanize, inr, time } from '../lib/format'

export default function TicketPage() {
  const tref = useTicketRef()
  const { id } = useParams()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['ticket', id], queryFn: () => api(`/v1/tickets/${id}`), refetchInterval: 3000 })
  const refresh = () => qc.invalidateQueries({ queryKey: ['ticket', id] })
  if (isLoading || !data) return <Spinner />
  const t = data.ticket
  const c = data.customer
  return (
    <div>
      <Link to="/inbox" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" />Inbox</Link>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xl font-semibold">{tref(t.number)} · {t.summary || t.subject}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <StatusBadge status={t.status} /><UrgencyBadge urgency={t.urgency} />
            {t.intent && <Badge>{humanize(t.intent)}</Badge>}<SentimentChip value={t.sentiment} />
            {t.language && <Badge tone="violet">{humanize(t.language)}</Badge>}
            {t.resolution_type && <Badge tone="brand"><Bot className="h-3 w-3" />{humanize(t.resolution_type)}</Badge>}
            {t.injection_suspected && <Badge tone="red"><ShieldAlert className="h-3 w-3" />Prompt injection flagged</Badge>}
            {t.sensitive && <Badge tone="red">Sensitive: {t.sensitive_categories?.join(', ')}</Badge>}
          </div>
        </div>
        <StatusActions id={t.id} status={t.status} onDone={refresh} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[260px_1fr_420px]">
        <CustomerRail data={data} />
        <Conversation data={data} onDone={refresh} />
        <AIPanel data={data} onDone={refresh} />
      </div>
      <div className="hidden">{c?.id}</div>
    </div>
  )
}

function StatusActions({ id, status, onDone }: { id: string; status: string; onDone: () => void }) {
  const m = useMutation({ mutationFn: (s: string) => api(`/v1/tickets/${id}/status`, { body: { status: s } }), onSuccess: onDone })
  return (
    <div className="flex gap-2">
      {status !== 'resolved' && <Button variant="secondary" size="sm" onClick={() => m.mutate('resolved')}><CheckCircle2 className="h-3.5 w-3.5" />Mark resolved</Button>}
      {status !== 'escalated' && <Button variant="ghost" size="sm" onClick={() => m.mutate('escalated')}>Take over</Button>}
    </div>
  )
}

function CustomerRail({ data }: { data: any }) {
  const tref = useTicketRef()
  const c = data.customer
  const t = data.ticket
  return (
    <div className="space-y-4">
      <Card title="Customer">
        <Link to={`/customers/${c.id}`} className="text-sm font-semibold text-brand-700 hover:underline">{c.display_name || 'Unknown'}</Link>
        <div className="mt-2 space-y-1 text-xs text-slate-600">
          {data.identities.map((i: any) => <div key={i.kind + i.value} className="flex justify-between gap-2"><span className="text-slate-400">{humanize(i.kind)}</span><span className="truncate">{i.value.split('|').pop()}</span></div>)}
        </div>
        <div className="mt-3 space-y-2 text-xs">
          <div className="flex items-center justify-between"><span className="text-slate-500">Frustration</span><FrustrationBar value={t.frustration_score} /></div>
          <div className="flex items-center justify-between"><span className="text-slate-500">Fraud score</span><span className={cx('font-medium tabular-nums', c.fraud_score >= 0.6 ? 'text-red-600' : 'text-slate-700')}>{(c.fraud_score || 0).toFixed(2)}</span></div>
          <div className="flex items-center justify-between"><span className="text-slate-500">Channels used</span><span className="flex gap-1">{data.channels.map((ch: string) => <ChannelIcon key={ch} channel={ch} />)}</span></div>
        </div>
      </Card>
      {!!t.order_refs?.length && <Card title="Orders on this ticket"><div className="flex flex-wrap gap-1">{t.order_refs.map((r: string) => <Badge key={r}>#{r}</Badge>)}</div></Card>}
      {!!data.other_tickets.length && (
        <Card title="Previous tickets">
          <div className="space-y-2">{data.other_tickets.map((o: any) => (
            <Link key={o.id} to={`/tickets/${o.id}`} className="block rounded-lg border border-slate-100 p-2 text-xs hover:bg-slate-50">
              <div className="flex justify-between"><span className="font-medium">{tref(o.number)}</span><span className="text-slate-400">{humanize(o.status)}</span></div>
              <div className="truncate text-slate-500">{o.summary}</div>
            </Link>))}</div>
        </Card>
      )}
    </div>
  )
}

function Attachment({ a }: { a: any }) {
  if (a.kind === 'image') return (
    <a href={mediaUrl(a.id)} target="_blank"><img src={mediaUrl(a.id)} className="mt-1.5 max-h-48 rounded-lg border border-slate-200" /></a>
  )
  if (a.kind === 'audio') return <audio controls src={mediaUrl(a.id)} className="mt-1.5 h-9 w-60" />
  return <a href={mediaUrl(a.id)} target="_blank" className="mt-1 flex items-center gap-1 text-xs text-brand-700"><FileText className="h-3.5 w-3.5" />Attachment</a>
}

function Conversation({ data, onDone }: { data: any; onDone: () => void }) {
  const [text, setText] = useState('')
  const send = useMutation({ mutationFn: () => api(`/v1/tickets/${data.ticket.id}/reply`, { body: { text } }), onSuccess: () => { setText(''); onDone() } })
  return (
    <Card title="Conversation" pad={false} className="flex min-h-[560px] flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4 scroll-thin" style={{ maxHeight: 640 }}>
        {data.messages.map((m: any) => {
          const out = m.direction === 'out'
          return (
            <div key={m.id} className={cx('flex', out ? 'justify-end' : 'justify-start')}>
              <div className={cx('max-w-[80%] rounded-2xl px-3.5 py-2 text-sm shadow-sm', out ? (m.author_type === 'ai' ? 'bg-brand-600 text-white' : 'bg-slate-800 text-white') : 'border border-slate-200 bg-white')}>
                <div className={cx('mb-0.5 flex items-center gap-1.5 text-[10px]', out ? 'text-white/70' : 'text-slate-400')}>
                  {out ? (m.author_type === 'ai' ? <Bot className="h-3 w-3" /> : <User className="h-3 w-3" />) : <ChannelIcon channel={m.channel} className="h-3 w-3" />}
                  {out ? (m.author_name || humanize(m.author_type)) : channelName(m.channel)} · {time(m.created_at)}
                  {out && m.status === 'simulated' && <span>· simulated delivery</span>}
                </div>
                {m.text && <div className="whitespace-pre-wrap">{m.text}</div>}
                {m.transcript && <div className={cx('mt-1 flex items-start gap-1 text-xs italic', out ? 'text-white/80' : 'text-slate-500')}><Mic className="mt-0.5 h-3 w-3 shrink-0" />“{m.transcript}”</div>}
                {(m.attachments || []).map((a: any) => <Attachment key={a.id} a={a} />)}
              </div>
            </div>
          )
        })}
      </div>
      <div className="border-t border-slate-100 p-3">
        <div className="flex gap-2">
          <textarea className={inputCls + ' resize-none'} rows={2} placeholder="Reply as a human…" value={text} onChange={(e) => setText(e.target.value)} />
          <Button onClick={() => send.mutate()} disabled={!text.trim()} loading={send.isPending}><Send className="h-4 w-4" /></Button>
        </div>
      </div>
    </Card>
  )
}

function AIPanel({ data, onDone }: { data: any; onDone: () => void }) {
  const pendingDrafts = data.drafts.filter((d: any) => d.status === 'draft')
  const pendingApprovals = data.approvals.filter((a: any) => a.status === 'pending' && a.kind !== 'draft')
  return (
    <div className="space-y-4">
      {pendingDrafts.map((d: any) => <DraftCard key={d.id} d={d} onDone={onDone} />)}
      {pendingApprovals.map((a: any) => <ApprovalCard key={a.id} a={a} actions={data.actions} onDone={onDone} />)}
      {!!data.actions.length && (
        <Card title={<span className="flex items-center gap-1.5"><Wrench className="h-4 w-4 text-slate-400" />Actions & policy decisions</span>}>
          <div className="space-y-2">{data.actions.map((a: any) => <ActionRow key={a.id} a={a} />)}</div>
        </Card>
      )}
      <Evidence data={data} />
      {data.security_events.length > 0 && (
        <Card title={<span className="flex items-center gap-1.5 text-red-700"><ShieldAlert className="h-4 w-4" />Security</span>}>
          <div className="space-y-1.5 text-xs">{data.security_events.map((e: any) => <div key={e.id} className="rounded-md bg-red-50 p-2 text-red-800">{e.detail}</div>)}</div>
        </Card>
      )}
      <Card title={<span className="flex items-center gap-1.5"><Brain className="h-4 w-4 text-brand-600" />How Sahay handled it</span>} pad={false}>
        <div className="divide-y divide-slate-100">{[...data.runs].reverse().map((r: any, i: number) => <Run key={r.id} r={r} open={i === 0} />)}</div>
      </Card>
    </div>
  )
}

function DraftCard({ d, onDone }: { d: any; onDone: () => void }) {
  const [text, setText] = useState(d.text)
  const send = useMutation({ mutationFn: () => api(`/v1/drafts/${d.id}/send`, { body: { text } }), onSuccess: onDone })
  const discard = useMutation({ mutationFn: () => api(`/v1/drafts/${d.id}/discard`, { method: 'POST' }), onSuccess: onDone })
  return (
    <Card className="border-amber-300 ring-2 ring-amber-100" title={<span className="flex items-center gap-1.5"><Bot className="h-4 w-4 text-amber-600" />AI draft waiting for you</span>}>
      {d.guard?.problems?.length > 0 && <div className="mb-2 rounded-md bg-amber-50 p-2 text-xs text-amber-800">Safety check: {d.guard.problems.join('; ')}</div>}
      <textarea className={inputCls} rows={6} value={text} onChange={(e) => setText(e.target.value)} />
      <Citations c={d.citations} />
      <div className="mt-3 flex gap-2">
        <Button size="sm" onClick={() => send.mutate()} loading={send.isPending}><Send className="h-3.5 w-3.5" />{text !== d.text ? 'Send edited' : 'Approve & send'}</Button>
        <Button size="sm" variant="ghost" onClick={() => discard.mutate()}>Discard</Button>
      </div>
    </Card>
  )
}

function Citations({ c }: { c: any[] }) {
  if (!c?.length) return null
  return (
    <div className="mt-2 space-y-1">
      {c.map((x: any) => (
        <div key={x.ref} className="rounded-md border border-slate-100 bg-slate-50 p-2 text-[11px] text-slate-600" title={x.snippet}>
          <span className="mr-1 rounded bg-white px-1 font-semibold text-brand-700 ring-1 ring-slate-200">{x.ref}</span>{x.title}
          <div className="mt-0.5 line-clamp-2 text-slate-500">{x.snippet}</div>
        </div>
      ))}
    </div>
  )
}

function ApprovalCard({ a, actions, onDone }: { a: any; actions: any[]; onDone: () => void }) {
  const decide = useMutation({ mutationFn: (decision: string) => api(`/v1/approvals/${a.id}/decide`, { body: { decision } }), onSuccess: onDone })
  const act = actions.find((x) => x.id === a.action_id)
  return (
    <Card className="border-amber-300 ring-2 ring-amber-100" title={<span className="flex items-center gap-1.5 text-amber-800"><ShieldCheck className="h-4 w-4" />{a.kind === 'handoff' ? 'Handed to a human' : 'Approval needed'}</span>}>
      <div className="text-sm text-slate-800">{a.summary}</div>
      {a.suggestion && <div className="mt-1 text-xs text-slate-500">{a.suggestion}</div>}
      {act && <div className="mt-2 text-xs text-slate-500">Value: <b>{inr(act.value_paise, true)}</b></div>}
      <div className="mt-3 flex gap-2">
        {a.kind === 'action' ? <>
          <Button size="sm" variant="success" onClick={() => decide.mutate('approve')} loading={decide.isPending}>Approve</Button>
          <Button size="sm" variant="secondary" onClick={() => decide.mutate('reject')}>Reject</Button>
        </> : <Button size="sm" onClick={() => decide.mutate('approve')} loading={decide.isPending}>I'll take it</Button>}
      </div>
    </Card>
  )
}

function ActionRow({ a }: { a: any }) {
  const [open, setOpen] = useState(false)
  const tone = { succeeded: 'green', pending_approval: 'amber', denied: 'red', rejected: 'red', failed: 'red', approved: 'blue' }[a.status as string] || 'slate'
  const icon = a.status === 'succeeded' ? <CheckCircle2 className="h-3 w-3" /> : a.status === 'denied' || a.status === 'rejected' ? <XCircle className="h-3 w-3" /> : null
  return (
    <div className="rounded-lg border border-slate-100 p-2.5 text-xs">
      <button className="flex w-full items-center justify-between gap-2" onClick={() => setOpen(!open)}>
        <span className="flex items-center gap-1.5 font-medium text-slate-800">{open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}{humanize(a.type)} {a.value_paise > 0 && <span className="text-slate-500">· {inr(a.value_paise, true)}</span>}</span>
        <span className="flex items-center gap-1"><Badge tone={tone}>{icon}{humanize(a.status)}</Badge><Badge>{a.proposed_by === 'ai' ? 'AI' : humanize(a.proposed_by)}</Badge>{a.dry_run && <Badge>dry run</Badge>}</span>
      </button>
      <div className="mt-1.5 text-slate-600"><span className="font-medium">Gate: </span>{humanize(a.decision)} — {a.decision_reason}</div>
      {open && (
        <div className="mt-2 grid gap-2">
          <Pre label="Parameters (from AI)" v={a.params} />
          <Pre label="Facts (computed by server)" v={a.facts} />
          {a.result && <Pre label="Result" v={a.result} />}
        </div>
      )}
    </div>
  )
}

function Pre({ label, v }: { label: string; v: any }) {
  return <div><div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
    <pre className="max-h-48 overflow-auto rounded-md bg-slate-900 p-2 text-[11px] leading-relaxed text-slate-100 scroll-thin">{JSON.stringify(v, null, 2)}</pre></div>
}

function Evidence({ data }: { data: any }) {
  const atts = data.messages.flatMap((m: any) => (m.attachments || []).filter((a: any) => a.kind === 'image' && a.analysis))
  if (!atts.length) return null
  return (
    <Card title={<span className="flex items-center gap-1.5"><ImageIcon className="h-4 w-4 text-slate-400" />Photo evidence</span>}>
      {atts.map((a: any) => {
        const an = a.analysis
        return (
          <div key={a.id} className="space-y-2">
            <div className="flex gap-2">
              <img src={mediaUrl(a.id)} className="h-28 w-28 rounded-lg border object-cover" />
              <div className="space-y-1 text-xs">
                <div className="flex flex-wrap gap-1">
                  <Badge tone={an.photo_verified ? 'green' : 'red'}>{an.photo_verified ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}{an.photo_verified ? 'Damage verified' : 'Not verified'}</Badge>
                  <Badge tone={an.matches_ordered_product ? 'green' : 'red'}>{an.matches_ordered_product ? 'Matches product' : 'Product mismatch'}</Badge>
                  {an.reused_image && <Badge tone="red">Reused / catalog photo</Badge>}
                </div>
                <div className="text-slate-600">{humanize(an.damage_type)} · {an.severity}</div>
                <div className="text-slate-500">{an.notes}</div>
                <div>Fraud score <b className={an.fraud_score >= 0.6 ? 'text-red-600' : ''}>{an.fraud_score}</b></div>
              </div>
            </div>
            {an.fraud_signals?.length > 0 && <div className="space-y-1">{an.fraud_signals.map((s: any, i: number) => <div key={i} className="rounded bg-red-50 px-2 py-1 text-[11px] text-red-700">⚑ {humanize(s.kind)} (+{s.score}) {s.evidence?.sku ? `— matches catalog image of ${s.evidence.sku}` : ''}</div>)}</div>}
          </div>
        )
      })}
    </Card>
  )
}

function Run({ r, open: initial }: { r: any; open: boolean }) {
  const [open, setOpen] = useState(initial)
  const triage = r.steps.find((s: any) => s.type === 'triage')?.output
  return (
    <div className="p-3">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 font-medium text-slate-700">{open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}Run · {time(r.created_at)}</span>
        <span className="text-slate-400">{r.latency_ms ? `${(r.latency_ms / 1000).toFixed(1)}s` : r.status}</span>
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {triage && (
            <div className="rounded-lg bg-brand-50 p-2.5 text-xs">
              <div className="mb-1 font-semibold text-brand-800">1 · Triage</div>
              <div className="flex flex-wrap gap-1">
                <Badge>{humanize(triage.intent)}</Badge><UrgencyBadge urgency={triage.urgency} /><SentimentChip value={triage.sentiment} />
                <Badge tone="violet">{triage.language}</Badge>{triage.injection_suspected && <Badge tone="red">injection</Badge>}{triage.is_sensitive && <Badge tone="red">sensitive</Badge>}
              </div>
              {triage.transcript && <div className="mt-1 italic text-slate-600">🎙 {triage.transcript}</div>}
              {triage.image_notes?.map((n: string, i: number) => <div key={i} className="mt-1 text-slate-600">🖼 {n}</div>)}
              <div className="mt-1 text-slate-500">KB queries: {triage.search_queries_en?.join(' · ')}</div>
            </div>
          )}
          <div className="text-xs font-semibold text-slate-700">2 · Investigate & act</div>
          {r.steps.filter((s: any) => ['tool', 'action'].includes(s.type)).map((s: any) => <Step key={s.id} s={s} />)}
          {r.plan && (
            <div className="rounded-lg border border-slate-100 p-2.5 text-xs">
              <div className="mb-0.5 font-semibold text-slate-700">3 · Diagnosis</div>
              <div className="text-slate-600">{r.plan.diagnosis}</div>
              <div className="mt-1 flex gap-1">{r.plan.resolved && <Badge tone="green">resolved</Badge>}{r.plan.needs_human && <Badge tone="amber">needs human</Badge>}<Badge>{r.plan.sent ? 'auto-sent' : 'drafted'}</Badge><Badge>autonomy: {r.plan.autonomy}</Badge></div>
            </div>
          )}
          {r.error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{r.error}</div>}
        </div>
      )}
    </div>
  )
}

function Step({ s }: { s: any }) {
  const [open, setOpen] = useState(false)
  const out = s.output || {}
  const decision = out.decision
  return (
    <div className={cx('rounded-md border p-2 text-xs', s.type === 'action' ? 'border-brand-200 bg-brand-50/40' : 'border-slate-100')}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-slate-700">{s.type === 'action' ? '⚡' : '🔧'} {s.name}<span className="font-sans text-slate-400">{s.input && Object.keys(s.input).length ? `(${Object.values(s.input).map(String).join(', ').slice(0, 40)})` : ''}</span></span>
        <span className="flex items-center gap-1">
          {decision && <Badge tone={decision === 'allow' ? 'green' : decision === 'deny' ? 'red' : 'amber'}>{humanize(decision)}</Badge>}
          <span className="text-slate-400">{s.latency_ms}ms</span>
        </span>
      </button>
      {open && <div className="mt-1.5"><Pre label="Output" v={s.output} /></div>}
    </div>
  )
}

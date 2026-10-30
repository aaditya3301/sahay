import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, FlaskConical, History, ShieldAlert, Users, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, Card, cx, Empty, Field, inputCls, PageHeader, Spinner, Stat } from '../components/ui'
import { api } from '../lib/api'
import { humanize, time } from '../lib/format'

export default function Simulations() {
  const qc = useQueryClient()
  const nav = useNavigate()
  const { data: runs } = useQuery({ queryKey: ['sims'], queryFn: () => api('/v1/sims'), refetchInterval: 3000 })
  const { data: personas } = useQuery({ queryKey: ['personas'], queryFn: () => api('/v1/sims/personas') })
  const [chosen, setChosen] = useState<string[]>([])
  const [csv, setCsv] = useState<File | null>(null)
  const [limit, setLimit] = useState(15)
  const mystery = useMutation({ mutationFn: () => api('/v1/sims/mystery', { body: { personas: chosen, max_turns: 3 } }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['sims'] }); nav(`/simulations/${r.id}`) } })
  const shadow = useMutation({
    mutationFn: () => { const f = new FormData(); f.append('file', csv!); f.append('limit', String(limit)); return api('/v1/sims/shadow', { form: f }) },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['sims'] }); nav(`/simulations/${r.id}`) },
  })
  const toggle = (k: string) => setChosen(chosen.includes(k) ? chosen.filter((x) => x !== k) : [...chosen, k])
  return (
    <>
      <PageHeader title="Simulations" subtitle="Test your agent. No messages sent or actions taken." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={<span className="flex items-center gap-1.5"><Users className="h-4 w-4 text-brand-600" />AI mystery shoppers</span>}>
          <p className="mb-3 text-sm text-slate-500">Simulated customers — angry, confused, Hindi-only, fraudsters, prompt-injectors — chat with your agent. You get a readiness score and every failing transcript.</p>
          <div className="mb-3 grid grid-cols-2 gap-1.5">
            {personas?.map((p: any) => (
              <button key={p.key} onClick={() => toggle(p.key)} className={cx('rounded-lg border p-2 text-left text-xs', chosen.includes(p.key) ? 'border-brand-500 bg-brand-50' : 'border-slate-200 hover:bg-slate-50')}>
                <div className="flex items-center justify-between font-medium text-slate-800">{p.name}{p.safety_critical && <ShieldAlert className="h-3.5 w-3.5 text-red-500" />}</div>
              </button>
            ))}
          </div>
          <Button onClick={() => mystery.mutate()} loading={mystery.isPending}>Run {chosen.length || 'all'} personas</Button>
          <span className="ml-2 text-xs text-slate-400">~1–3 min</span>
        </Card>
        <Card title={<span className="flex items-center gap-1.5"><History className="h-4 w-4 text-brand-600" />Shadow mode (replay your past tickets)</span>}>
          <p className="mb-3 text-sm text-slate-500">Upload a CSV of past tickets. Sahay replays each one and a judge compares what it would have done with what your team did.</p>
          <div className="space-y-2">
            <Field label="CSV file" hint="Columns: customer_message, human_reply, outcome, order_ref (optional)"><input type="file" accept=".csv" className="text-sm" onChange={(e) => setCsv(e.target.files?.[0] || null)} /></Field>
            <Field label="How many rows to replay"><input type="number" className={inputCls + ' w-28'} value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></Field>
            <Button onClick={() => shadow.mutate()} disabled={!csv} loading={shadow.isPending}>Run shadow mode</Button>
            <a href="/public/sample-history.csv" className="ml-2 text-xs text-brand-700 underline">Download a sample CSV</a>
          </div>
        </Card>
      </div>
      <Card className="mt-4" title="Runs" pad={false}>
        {!runs?.length ? <Empty icon={<FlaskConical className="h-10 w-10" />} title="No simulations yet" /> : (
          <div className="divide-y divide-slate-100">
            {runs.map((r: any) => (
              <Link key={r.id} to={`/simulations/${r.id}`} className="flex items-center justify-between px-4 py-3 hover:bg-slate-50">
                <div><div className="text-sm font-medium">{r.kind === 'shadow' ? 'Shadow mode' : 'Mystery shoppers'} · {time(r.created_at)}</div>
                  <div className="text-xs text-slate-500">{r.finished}/{r.cases} cases done</div></div>
                <div className="flex items-center gap-2">
                  {r.readiness_score != null && <span className="text-lg font-semibold tabular-nums">{Math.round(r.readiness_score)}<span className="text-xs text-slate-400">/100</span></span>}
                  <Badge tone={r.status === 'done' ? 'green' : 'amber'}>{r.status}</Badge>
                </div>
              </Link>
            ))}
          </div>
        )}
      </Card>
    </>
  )
}

export function SimRun() {
  const { id } = useParams()
  const { data } = useQuery({ queryKey: ['sim', id], queryFn: () => api(`/v1/sims/${id}`), refetchInterval: (q) => (q.state.data?.run?.status === 'done' ? false : 3000) })
  const [open, setOpen] = useState<string | null>(null)
  if (!data) return <Spinner />
  const { run, cases } = data
  const s = run.summary || {}
  return (
    <>
      <Link to="/simulations" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" />Simulations</Link>
      <PageHeader title={run.kind === 'shadow' ? 'Shadow mode report' : 'Mystery shopper report'} subtitle={run.status === 'done' ? `Finished · ${time(run.created_at)}` : `Running… ${cases.filter((c: any) => c.status !== 'running' && c.status !== 'pending').length}/${cases.length} cases done`} />
      {run.status === 'done' && (
        <div className="mb-4 grid gap-3 md:grid-cols-4">
          {run.kind === 'shadow' ? <>
            <Stat label="Would auto-resolve" value={`${s.auto_resolved_pct}%`} sub={`${s.drafted_pct}% drafted · ${s.escalated_pct}% escalated`} />
            <Stat label="Same outcome as your team" value={`${s.matched_human_pct}%`} />
            <Stat label="Auto-resolved & correct" value={`${s.auto_correct_pct}%`} />
            <Stat label="Would have been wrong" value={s.wrong} sub="see the red cases below" />
          </> : <>
            <Stat label="Readiness score" value={<>{Math.round(run.readiness_score)}<span className="text-sm text-slate-400">/100</span></>} sub="safety-critical personas count ×3" />
            <Stat label="Passed" value={`${s.passed}/${s.cases}`} />
            <Stat label="Safety failures" value={s.safety_failures} sub={s.safety_failures ? 'fix before going live' : 'none — money & data stayed safe'} />
            <Stat label="Avg quality (judge)" value={`${s.avg_quality}/5`} />
          </>}
        </div>
      )}
      {s.top_issues?.length > 0 && <Card className="mb-4" title="What to fix"><ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">{s.top_issues.map((i: string, k: number) => <li key={k}>{i}</li>)}</ul></Card>}
      <Card title="Cases" pad={false}>
        <div className="divide-y divide-slate-100">
          {cases.map((c: any) => (
            <div key={c.id}>
              <button onClick={() => setOpen(open === c.id ? null : c.id)} className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-50">
                <div className="flex items-center gap-2">
                  {c.status === 'running' || c.status === 'pending' ? <span className="h-4 w-4 animate-pulse rounded-full bg-amber-300" /> : c.passed ? <CheckCircle2 className="h-4 w-4 text-good" /> : <XCircle className="h-4 w-4 text-critical" />}
                  <div><div className="text-sm font-medium">{c.persona.name}{c.persona.safety_critical && <Badge tone="red" className="ml-1.5">safety</Badge>}</div>
                    <div className="max-w-2xl truncate text-xs text-slate-500">{c.persona.key === 'shadow' ? c.persona.customer_message : c.judge?.summary || c.persona.expect}</div></div>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  {c.judge?.mode && <Badge>{humanize(c.judge.mode)}</Badge>}
                  {c.judge?.avg && <span className="tabular-nums text-slate-500">{c.judge.avg}/5</span>}
                  {c.safety_failures?.map((f: string) => <Badge key={f} tone="red">{f}</Badge>)}
                </div>
              </button>
              {open === c.id && (
                <div className="grid gap-4 bg-slate-50 px-4 py-3 md:grid-cols-[1fr_320px]">
                  <div className="space-y-1.5">{c.transcript.map((m: any, i: number) => (
                    <div key={i} className={cx('rounded-lg px-3 py-1.5 text-sm', m.role === 'customer' ? 'mr-12 bg-white' : 'ml-12 bg-brand-50', m.role === 'agent_draft' && 'border border-dashed border-amber-300')}>
                      <div className="text-[10px] uppercase text-slate-400">{m.role === 'agent_draft' ? 'agent (draft, not sent)' : m.role}</div>{m.text}</div>))}</div>
                  <div className="space-y-2 text-xs">
                    {c.judge && !c.judge.error && (c.persona.key === 'shadow' ? (
                      <div className="rounded-lg bg-white p-2.5"><b>Judge:</b> {c.judge.reason}<div className="mt-1 text-slate-500">Human did: {c.persona.human_reply || c.persona.outcome}</div></div>
                    ) : (
                      <div className="rounded-lg bg-white p-2.5">
                        {['correctness', 'policy_compliance', 'tone', 'language_match', 'resolution'].map((k) => <div key={k} className="flex justify-between"><span>{humanize(k)}</span><b>{c.judge[k]}/5</b></div>)}
                        {c.judge.issues?.length > 0 && <ul className="mt-1.5 list-disc pl-4 text-slate-600">{c.judge.issues.map((i: string) => <li key={i}>{i}</li>)}</ul>}
                      </div>
                    ))}
                    {c.actions?.length > 0 && <div className="rounded-lg bg-white p-2.5"><b>Actions attempted:</b>{c.actions.map((a: any, i: number) => <div key={i}>{humanize(a.type)} → {a.decision} ({a.status})</div>)}</div>}
                    {c.judge?.error && <div className="text-red-600">{c.judge.error}</div>}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </>
  )
}

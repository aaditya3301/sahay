import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Rocket, X } from 'lucide-react'
import { Badge, Button, Card, PageHeader, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { humanize } from '../lib/format'

export default function Learning() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['learning'], queryFn: () => api('/v1/learning') })
  const refresh = () => qc.invalidateQueries({ queryKey: ['learning'] })
  const extract = useMutation({ mutationFn: () => api('/v1/learning/extract', { method: 'POST' }), onSuccess: refresh })
  const decide = useMutation({ mutationFn: ({ id, decision }: any) => api(`/v1/learning/lessons/${id}`, { body: { decision } }), onSuccess: refresh })
  const setMode = useMutation({ mutationFn: ({ intent, mode }: any) => api('/v1/settings/autonomy', { body: { intent, mode } }), onSuccess: refresh })
  if (isLoading || !data) return <Spinner />
  return (
    <>
      <PageHeader title="Learning" subtitle="Help your agent learn from your team’s edits." />
      <div className="learning-layout grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="learning-lessons space-y-4">
          <Card title="Lessons from your team's edits" action={<Button size="sm" variant="secondary" onClick={() => extract.mutate()} loading={extract.isPending}>Learn from recent edits</Button>}>
            {!data.lessons.length ? <div className="learning-empty-state"><strong>No lessons yet</strong><span>Edit an AI draft in a ticket, then select “Learn from recent edits”.</span></div> : (
              <div className="space-y-2">{data.lessons.map((l: any) => (
                <div key={l.id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 p-2.5">
                  <div><div className="text-sm text-slate-800">{l.lesson}</div><div className="mt-0.5 flex gap-1">{l.intent && <Badge>{humanize(l.intent)}</Badge>}<Badge tone={l.status === 'approved' ? 'green' : l.status === 'rejected' ? 'slate' : 'amber'}>{l.status}</Badge></div></div>
                  {l.status === 'suggested' && <div className="flex shrink-0 gap-1"><Button size="sm" variant="success" onClick={() => decide.mutate({ id: l.id, decision: 'approve' })}>Approve</Button><Button size="sm" variant="ghost" title="Reject lesson" onClick={() => decide.mutate({ id: l.id, decision: 'reject' })}><X className="h-3.5 w-3.5" aria-hidden="true" /><span className="sr-only">Reject lesson</span></Button></div>}
                </div>))}</div>
            )}
          </Card>
          <Card title="Recent edits">
            {!data.edits.length ? <div className="learning-recent-empty">No edited drafts yet.</div> : data.edits.map((e: any, i: number) => (
              <div key={i} className="mb-3 grid gap-2 text-xs md:grid-cols-2">
                <div className="rounded-md bg-red-50 p-2 text-slate-700"><div className="mb-0.5 font-semibold text-red-700">AI draft</div>{e.original_text}</div>
                <div className="rounded-md bg-emerald-50 p-2 text-slate-700"><div className="mb-0.5 font-semibold text-emerald-700">Sent by team</div>{e.final_text}</div>
              </div>))}
          </Card>
        </div>
        <div className="learning-autonomy space-y-4">
          {data.autonomy_suggestions.map((s: any) => (
            <Card key={s.intent} className="border-emerald-300 ring-2 ring-emerald-100" title={<span className="flex items-center gap-1.5"><Rocket className="h-4 w-4 text-emerald-600" />Ready for more autonomy</span>}>
              <div className="text-sm">{humanize(s.intent)} replies were approved unchanged <b>{s.approved_unchanged}/{s.total}</b> times ({s.rate_pct}%).</div>
              <Button size="sm" className="mt-2" variant="success" onClick={() => setMode.mutate({ intent: s.intent, mode: 'auto' })}>Switch to auto-send</Button>
            </Card>
          ))}
          <Card title="Autonomy by intent">
            <div className="learning-autonomy-help">Choose how the agent handles each kind of message. Auto sends replies, Draft waits for your review, and Human routes it to your team. Your policies still govern money actions.</div>
            <div className="learning-autonomy-grid">{data.intents.map((i: string) => {
              const mode = data.autonomy?.[i] || data.default_autonomy
              return (
                <label key={i} className="learning-autonomy-row">
                  <span>{humanize(i)}</span>
                  <select aria-label={`Autonomy for ${humanize(i)}`} className="rounded-md border border-slate-200 px-2 py-1 text-xs" value={mode} onChange={(e) => setMode.mutate({ intent: i, mode: e.target.value })}>
                    <option value="auto">Auto</option><option value="draft">Draft</option><option value="human">Human</option>
                  </select>
                </label>)
            })}</div>
          </Card>
        </div>
      </div>
    </>
  )
}

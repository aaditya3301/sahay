import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GraduationCap, Rocket } from 'lucide-react'
import { Badge, Button, Card, Empty, PageHeader, Spinner } from '../components/ui'
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
      <div className="grid gap-4 lg:grid-cols-[1fr_420px]">
        <div className="space-y-4">
          <Card title="Lessons from your team's edits" action={<Button size="sm" variant="secondary" onClick={() => extract.mutate()} loading={extract.isPending}>Learn from recent edits</Button>}>
            {!data.lessons.length ? <Empty icon={<GraduationCap className="h-10 w-10" />} title="No lessons yet">Edit an AI draft in a ticket before sending it, then click “Learn from recent edits”.</Empty> : (
              <div className="space-y-2">{data.lessons.map((l: any) => (
                <div key={l.id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 p-2.5">
                  <div><div className="text-sm text-slate-800">{l.lesson}</div><div className="mt-0.5 flex gap-1">{l.intent && <Badge>{humanize(l.intent)}</Badge>}<Badge tone={l.status === 'approved' ? 'green' : l.status === 'rejected' ? 'slate' : 'amber'}>{l.status}</Badge></div></div>
                  {l.status === 'suggested' && <div className="flex shrink-0 gap-1"><Button size="sm" variant="success" onClick={() => decide.mutate({ id: l.id, decision: 'approve' })}>Approve</Button><Button size="sm" variant="ghost" onClick={() => decide.mutate({ id: l.id, decision: 'reject' })}>✕</Button></div>}
                </div>))}</div>
            )}
          </Card>
          <Card title="Recent edits">
            {!data.edits.length ? <div className="text-sm text-slate-400">No edited drafts yet.</div> : data.edits.map((e: any, i: number) => (
              <div key={i} className="mb-3 grid gap-2 text-xs md:grid-cols-2">
                <div className="rounded-md bg-red-50 p-2 text-slate-700"><div className="mb-0.5 font-semibold text-red-700">AI draft</div>{e.original_text}</div>
                <div className="rounded-md bg-emerald-50 p-2 text-slate-700"><div className="mb-0.5 font-semibold text-emerald-700">Sent by team</div>{e.final_text}</div>
              </div>))}
          </Card>
        </div>
        <div className="space-y-4">
          {data.autonomy_suggestions.map((s: any) => (
            <Card key={s.intent} className="border-emerald-300 ring-2 ring-emerald-100" title={<span className="flex items-center gap-1.5"><Rocket className="h-4 w-4 text-emerald-600" />Ready for more autonomy</span>}>
              <div className="text-sm">{humanize(s.intent)} replies were approved unchanged <b>{s.approved_unchanged}/{s.total}</b> times ({s.rate_pct}%).</div>
              <Button size="sm" className="mt-2" variant="success" onClick={() => setMode.mutate({ intent: s.intent, mode: 'auto' })}>Switch to auto-send</Button>
            </Card>
          ))}
          <Card title="Autonomy by intent">
            <div className="mb-2 text-xs text-slate-500">Auto = send without review · Draft = AI writes, you approve · Human = AI stays out. Money actions are always governed by your policies.</div>
            <div className="space-y-1.5">{data.intents.map((i: string) => {
              const mode = data.autonomy?.[i] || data.default_autonomy
              return (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span>{humanize(i)}</span>
                  <select className="rounded-md border border-slate-200 px-2 py-1 text-xs" value={mode} onChange={(e) => setMode.mutate({ intent: i, mode: e.target.value })}>
                    <option value="auto">Auto</option><option value="draft">Draft</option><option value="human">Human</option>
                  </select>
                </div>)
            })}</div>
          </Card>
        </div>
      </div>
    </>
  )
}

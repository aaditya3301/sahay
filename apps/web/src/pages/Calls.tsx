import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PhoneCall, PhoneIncoming, PhoneOutgoing } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, cx, Empty, Modal, PageHeader, Spinner } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize, time } from '../lib/format'

const purposeLabel = (p?: string) => (p === 'web_call' ? 'Website voice' : humanize(p))

const outcomeTone = (o?: string) => ({ resolved: 'green', cod_confirmed: 'green', lead_captured: 'violet', escalated: 'red', follow_up: 'amber', no_answer: 'slate', cancel_requested: 'orange' } as any)[o || ''] || 'slate'

export default function Calls() {
  const tref = useTicketRef()
  const [params, setParams] = useSearchParams()
  const { data, isLoading } = useQuery({ queryKey: ['calls'], queryFn: () => api('/v1/calls'), refetchInterval: 5000 })
  const sel = params.get('id')
  const [, force] = useState(0)
  const qc = useQueryClient()
  const [dialErr, setDialErr] = useState<Record<string, string>>({})
  const dial = useMutation({
    mutationFn: (id: string) => api(`/v1/calls/${id}/dial`, { method: 'POST' }),
    onSuccess: (_, id) => { setDialErr((e) => ({ ...e, [id]: '' })); qc.invalidateQueries({ queryKey: ['calls'] }) },
    onError: (e: Error, id) => setDialErr((x) => ({ ...x, [id]: e.message })),
  })
  const open = (id: string | null) => { if (id) setParams({ id }); else setParams({}); force((x) => x + 1) }
  const queued = (data || []).filter((c: any) => c.status === 'queued')
  const rest = (data || []).filter((c: any) => c.status !== 'queued')
  return (
    <>
      <PageHeader title="Calls" subtitle="Your customer calls, in one place."
        actions={<Link to="/studio"><Button variant="secondary"><PhoneCall className="h-4 w-4" />Test a call in your browser</Button></Link>} />
      {isLoading ? <Spinner /> : (
        <div className="space-y-4">
          {queued.length > 0 && (
            <Card title={`To call (${queued.length})`} pad={false}>
              <div className="divide-y divide-slate-100">{queued.map((c: any) => (
                <div key={c.id} className="flex items-center justify-between px-4 py-2.5 text-sm">
                  <div className="flex items-center gap-2"><PhoneOutgoing className="h-4 w-4 text-amber-600" /><span className="font-medium">{c.display_name || c.to_number}</span><Badge tone="amber">{purposeLabel(c.purpose)}</Badge></div>
                  <div className="flex items-center gap-2">
                    {dialErr[c.id] && <span className="max-w-[260px] truncate text-xs text-red-600" title={dialErr[c.id]}>{dialErr[c.id]}</span>}
                    <span className="text-xs text-slate-500">due {time(c.scheduled_for)}</span>
                    <Button size="sm" variant="secondary" loading={dial.isPending && dial.variables === c.id} onClick={() => dial.mutate(c.id)}><PhoneOutgoing className="h-3.5 w-3.5" />Call now</Button>
                  </div>
                </div>))}</div>
              <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">Due calls are dialled automatically through Sarvam when it's connected (Agent Studio → Voice agent), otherwise handed to your voice pipeline webhook.</div>
            </Card>
          )}
          <Card pad={false}>
            {!rest.length ? <Empty icon={<PhoneCall className="h-10 w-10" />} title="No calls yet">Try a test call from Agent Studio → Voice agent, or connect Naaradh with the Voice API.</Empty> : (
              <table className="w-full text-sm">
                <thead className="border-b bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-4 py-2">Caller</th><th>Purpose</th><th>Status</th><th>Outcome</th><th>Summary</th><th>Turns</th><th>When</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {rest.map((c: any) => (
                    <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => open(c.id)}>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-1.5 font-medium">{c.direction === 'inbound' ? <PhoneIncoming className="h-3.5 w-3.5 text-emerald-600" /> : <PhoneOutgoing className="h-3.5 w-3.5 text-amber-600" />}{c.display_name || 'Unknown caller'}</div>
                        <div className="text-xs text-slate-500">{c.direction === 'inbound' ? c.from_number : c.to_number}</div>
                      </td>
                      <td className="text-xs">{purposeLabel(c.purpose)}</td>
                      <td><Badge tone={c.status === 'in_progress' ? 'green' : 'slate'}>{humanize(c.status)}</Badge></td>
                      <td>{c.outcome ? <Badge tone={outcomeTone(c.outcome)}>{humanize(c.outcome)}</Badge> : '—'}</td>
                      <td className="max-w-[320px] truncate text-xs text-slate-600" title={c.summary}>{c.summary || '—'}</td>
                      <td className="tabular-nums text-xs">{c.turns}</td>
                      <td className="text-xs text-slate-500">{ago(c.started_at || c.created_at)}{c.ticket_number && <> · <span className="text-brand-700">{tref(c.ticket_number)}</span></>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      )}
      {sel && <CallDetail id={sel} onClose={() => open(null)} />}
    </>
  )
}

function CallDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const tref = useTicketRef()
  const { data: c } = useQuery({ queryKey: ['call', id], queryFn: () => api(`/v1/calls/${id}`), refetchInterval: (q) => (q.state.data?.status === 'in_progress' ? 2000 : false) })
  return (
    <Modal open onClose={onClose} title={c ? `${humanize(c.direction)} call · ${c.display_name || 'Unknown caller'}` : 'Call'} wide>
      {!c ? <Spinner /> : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge>{purposeLabel(c.purpose)}</Badge>{c.outcome && <Badge tone={outcomeTone(c.outcome)}>{humanize(c.outcome)}</Badge>}
            <span className="text-slate-500">{time(c.started_at || c.created_at)}</span>
            {c.ticket_id && <Link to={`/tickets/${c.ticket_id}`} className="text-xs text-brand-700 underline">Ticket {tref(c.ticket_number)}</Link>}
            {c.customer_id && <Link to={`/customers/${c.customer_id}`} className="text-xs text-brand-700 underline">Customer profile</Link>}
          </div>
          {c.summary && <div className="rounded-lg bg-slate-50 p-3 text-sm"><div className="text-slate-800">{c.summary}</div>{c.next_step && <div className="mt-1 text-xs text-slate-600">Next step: {c.next_step}</div>}</div>}
          <div className="max-h-[50vh] space-y-2 overflow-y-auto rounded-lg border border-slate-100 p-3 scroll-thin">
            {(c.transcript || []).map((m: any, i: number) => (
              <div key={i} className={cx('flex', m.role === 'caller' ? 'justify-end' : 'justify-start')}>
                <div className={cx('max-w-[80%] rounded-xl px-3 py-2 text-sm', m.role === 'caller' ? 'bg-brand-600 text-white' : 'border border-slate-200 bg-white')}>
                  <div className="mb-0.5 text-[10px] opacity-60">{m.role === 'caller' ? 'Caller' : 'Voice agent'}</div>{m.text}
                  {!!m.tools?.length && <div className="mt-1 flex flex-wrap gap-1">{m.tools.map((t: string, j: number) => <Badge key={j} tone="violet">{t}</Badge>)}</div>}
                </div>
              </div>
            ))}
            {!c.transcript?.length && <div className="text-center text-xs text-slate-400">No transcript.</div>}
          </div>
          {c.context && (c.context.orders_on_file || c.context.orders?.length || c.context.open_tickets?.length) ? (
            <div className="text-xs text-slate-500">Known before hello: {c.context.orders_on_file ?? c.context.orders?.length ?? 0} orders (details after the caller verifies an order ID), {c.context.open_tickets?.length || 0} open tickets{c.context.lead ? `, lead (${c.context.lead.stage})` : ''}.</div>
          ) : null}
        </div>
      )}
    </Modal>
  )
}

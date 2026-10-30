import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckSquare } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Empty, FrustrationBar, PageHeader, Spinner, Tabs } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize, inr } from '../lib/format'

export default function Approvals() {
  const tref = useTicketRef()
  const qc = useQueryClient()
  const [status, setStatus] = useState('pending')
  const { data, isLoading } = useQuery({ queryKey: ['approvals', status], queryFn: () => api(`/v1/approvals?status=${status}`), refetchInterval: 3000 })
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: string }) => api(`/v1/approvals/${id}/decide`, { body: { decision } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  })
  const sendDraft = useMutation({ mutationFn: (id: string) => api(`/v1/drafts/${id}/send`, { body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }) })
  return (
    <>
      <PageHeader title="Approvals" subtitle="Actions and replies waiting for approval." />
      <Card pad={false}>
        <div className="px-3 pt-1"><Tabs value={status} onChange={setStatus} tabs={[{ id: 'pending', label: 'Pending' }, { id: 'approved', label: 'Approved' }, { id: 'rejected', label: 'Rejected' }]} /></div>
        {isLoading ? <Spinner /> : !data?.length ? <Empty icon={<CheckSquare className="h-10 w-10" />} title="Nothing waiting">Actions that need your approval will appear here.</Empty> : (
          <div className="divide-y divide-slate-100">
            {data.map((a: any) => (
              <div key={a.id} className="grid grid-cols-[1fr_auto] gap-4 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={a.kind === 'action' ? 'amber' : a.kind === 'handoff' ? 'red' : 'blue'}>{a.kind === 'action' ? humanize(a.action_type) : a.kind === 'handoff' ? 'Handoff to human' : 'Reply draft'}</Badge>
                    {a.value_paise > 0 && <Badge>{inr(a.value_paise, true)}</Badge>}
                    <Link to={`/tickets/${a.ticket_id}`} className="text-xs font-medium text-brand-700 hover:underline">Ticket {tref(a.number)}</Link>
                    <span className="text-xs text-slate-400">· {a.customer_name} · {ago(a.created_at)}</span>
                    {a.decided_via && <Badge tone="violet">via {a.decided_via}</Badge>}
                  </div>
                  <div className="mt-1 text-sm text-slate-800">{a.summary}</div>
                  {a.draft_text && <div className="mt-1 line-clamp-2 rounded-md bg-slate-50 p-2 text-xs text-slate-600">{a.draft_text}</div>}
                  {a.suggestion && <div className="mt-1 text-xs text-slate-500">{a.suggestion}</div>}
                </div>
                <div className="flex flex-col items-end gap-2">
                  <FrustrationBar value={a.frustration_score} />
                  {status === 'pending' && (a.kind === 'action' ? (
                    <div className="flex gap-1.5">
                      <Button size="sm" variant="success" onClick={() => decide.mutate({ id: a.id, decision: 'approve' })}>Approve</Button>
                      <Button size="sm" variant="secondary" onClick={() => decide.mutate({ id: a.id, decision: 'reject' })}>Reject</Button>
                    </div>
                  ) : a.kind === 'draft' ? (
                    <div className="flex gap-1.5">
                      <Button size="sm" onClick={() => sendDraft.mutate(a.draft_id)}>Send as is</Button>
                      <Link to={`/tickets/${a.ticket_id}`}><Button size="sm" variant="secondary">Edit</Button></Link>
                    </div>
                  ) : <Link to={`/tickets/${a.ticket_id}`}><Button size="sm">Open</Button></Link>)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  )
}

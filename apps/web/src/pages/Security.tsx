import { useQuery } from '@tanstack/react-query'
import { Lock, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Card, Empty, PageHeader, Spinner, Stat, Tabs } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { humanize, time } from '../lib/format'

export default function Security() {
  const tref = useTicketRef()
  const { data, isLoading } = useQuery({ queryKey: ['security'], queryFn: () => api('/v1/security'), refetchInterval: 4000 })
  const [tab, setTab] = useState('events')
  if (isLoading || !data) return <Spinner />
  const count = (k: string) => data.events.filter((e: any) => e.kind === k).length
  return (
    <>
      <PageHeader title="Security" subtitle="Review safeguards and security activity." />
      <div className="mb-4 grid gap-3 md:grid-cols-4">
        <Stat label="Injection attempts blocked" value={count('prompt_injection')} icon={<ShieldAlert className="h-4 w-4" />} />
        <Stat label="Actions denied by code" value={data.denied_actions.length} icon={<Lock className="h-4 w-4" />} />
        <Stat label="Replies stopped by guard" value={count('reply_blocked')} icon={<ShieldCheck className="h-4 w-4" />} />
        <Stat label="PII redacted before AI" value={count('pii_redacted')} icon={<Lock className="h-4 w-4" />} />
      </div>
      <Card pad={false}>
        <div className="px-3 pt-1"><Tabs value={tab} onChange={setTab} tabs={[{ id: 'events', label: 'Security events' }, { id: 'denied', label: 'Denied actions' }, { id: 'audit', label: 'Audit log' }]} /></div>
        {tab === 'events' && (!data.events.length ? <Empty icon={<ShieldCheck className="h-10 w-10" />} title="All quiet" /> : (
          <div className="divide-y divide-slate-100">{data.events.map((e: any) => (
            <div key={e.id} className="flex items-start justify-between gap-3 px-4 py-2.5">
              <div><div className="flex items-center gap-1.5"><Badge tone={e.severity === 'high' ? 'red' : e.severity === 'medium' ? 'amber' : 'slate'}>{humanize(e.kind)}</Badge>
                {e.ticket_id && <Link to={`/tickets/${e.ticket_id}`} className="text-xs text-brand-700 hover:underline">{tref(e.number)}</Link>}</div>
                <div className="mt-0.5 text-sm text-slate-700">{e.detail}</div></div>
              <div className="shrink-0 text-xs text-slate-400">{time(e.created_at)}</div>
            </div>))}</div>
        ))}
        {tab === 'denied' && (!data.denied_actions.length ? <Empty icon={<Lock className="h-10 w-10" />} title="No denied actions" /> : (
          <table className="w-full text-sm"><thead className="bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-4 py-2">Action</th><th>Params the AI asked for</th><th>Why code said no</th><th>Ticket</th><th>When</th></tr></thead>
            <tbody className="divide-y divide-slate-100">{data.denied_actions.map((a: any, i: number) => (
              <tr key={i}><td className="px-4 py-2 font-medium">{humanize(a.type)}{a.dry_run && <Badge className="ml-1">sim</Badge>}</td>
                <td className="font-mono text-[11px] text-slate-600">{JSON.stringify(a.params)}</td>
                <td className="text-xs text-red-700">{a.decision_reason}</td>
                <td><Link to={`/tickets/${a.ticket_id}`} className="text-xs text-brand-700">{tref(a.number)}</Link></td>
                <td className="text-xs text-slate-400">{time(a.created_at)}</td></tr>))}</tbody></table>
        ))}
        {tab === 'audit' && (
          <div className="divide-y divide-slate-100">{data.audit.map((a: any) => (
            <div key={a.id} className="flex items-center justify-between gap-3 px-4 py-2 text-xs">
              <div><Badge>{a.actor_type}</Badge> <span className="font-medium text-slate-700">{a.actor || ''}</span> <span className="text-slate-600">{a.verb}</span> <span className="text-slate-400">{a.object_type}</span></div>
              <div className="text-slate-400">{time(a.created_at)}</div>
            </div>))}</div>
        )}
      </Card>
    </>
  )
}

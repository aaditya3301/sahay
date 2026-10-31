import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Users } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Card, ChannelIcon, Empty, FrustrationBar, inputCls, PageHeader, Spinner } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize, inr, time } from '../lib/format'
import { ScorePill, stageTone } from './Leads'

export default function Customers() {
  const [q, setQ] = useState('')
  const { data, isLoading } = useQuery({ queryKey: ['customers', q], queryFn: () => api(`/v1/customers?q=${encodeURIComponent(q)}`) })
  return (
    <>
      <PageHeader title="Customers" subtitle="Every customer and their conversation history." actions={<input className={inputCls + ' w-64'} placeholder="Search name, phone, email" value={q} onChange={(e) => setQ(e.target.value)} />} />
      <Card pad={false}>
        {isLoading ? <Spinner /> : !data?.length ? <Empty icon={<Users className="h-10 w-10" />} title="No customers yet" /> : (
          <table className="w-full text-sm">
            <thead className="border-b bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-4 py-2">Customer</th><th>Contact</th><th>Tickets</th><th>Frustration</th><th>Fraud</th><th>Last contact</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((c: any) => (
                <tr key={c.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2.5"><Link to={`/customers/${c.id}`} className="font-medium text-brand-700 hover:underline">{c.display_name || 'Unknown'}</Link>{c.vip && <Badge tone="violet" className="ml-1">VIP</Badge>}{c.signed_up_at && <Badge tone="blue" className="ml-1">Storefront account</Badge>}</td>
                  <td className="text-xs text-slate-500">{c.primary_phone}<br />{c.primary_email}</td>
                  <td className="tabular-nums">{c.tickets}</td>
                  <td><FrustrationBar value={c.frustration_score} /></td>
                  <td className="tabular-nums">{(c.fraud_score || 0).toFixed(2)}</td>
                  <td className="text-xs text-slate-500">{ago(c.last_contact)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  )
}

export function CustomerDetail() {
  const tref = useTicketRef()
  const { id } = useParams()
  const { data, isLoading } = useQuery({ queryKey: ['customer', id], queryFn: () => api(`/v1/customers/${id}`) })
  if (isLoading || !data) return <Spinner />
  const c = data.customer
  return (
    <>
      <Link to="/customers" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" />Customers</Link>
      <PageHeader title={c.display_name || 'Unknown customer'} subtitle={[c.primary_phone, c.primary_email].filter(Boolean).join(' · ')} />
      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        <Card title="Timeline (all channels)">
          <div className="space-y-2">
            {data.timeline.map((e: any, i: number) => (
              <div key={i} className="flex gap-3 text-sm">
                <div className="w-28 shrink-0 text-xs text-slate-400">{time(e.at)}</div>
                <div className="mt-0.5">{e.kind === 'message' ? <ChannelIcon channel={e.channel} /> : <span className="text-xs">{e.kind === 'action' ? '⚡' : '🎫'}</span>}</div>
                <div className="min-w-0 flex-1">
                  <span className={e.direction === 'out' ? 'text-brand-700' : 'text-slate-800'}>{e.direction === 'out' ? '↩ ' : ''}{e.text}</span>
                  {e.ticket && <span className="ml-1 text-xs text-slate-400">{tref(e.ticket)}</span>}
                </div>
              </div>
            ))}
          </div>
        </Card>
        <div className="space-y-4">
          <Journey id={id!} />
          <Card title="Signals">
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-slate-500">Frustration</span><FrustrationBar value={c.frustration_score} /></div>
              <div className="flex justify-between"><span className="text-slate-500">Fraud score</span><b>{(c.fraud_score || 0).toFixed(2)}</b></div>
              <div className="flex justify-between"><span className="text-slate-500">Language</span><span>{humanize(c.language_pref) || '—'}</span></div>
            </div>
            {data.fraud_signals.map((f: any, i: number) => <div key={i} className="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">⚑ {humanize(f.kind)} · {time(f.created_at)}</div>)}
          </Card>
          {data.shopper && (
            <Card title="Storefront account">
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-slate-500">Signed up</span><span>{time(data.shopper.created_at)}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Last login</span><span>{data.shopper.last_login_at ? time(data.shopper.last_login_at) : '—'}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Orders placed</span><span>{data.orders.length}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Total ordered</span><b>{inr(data.orders.reduce((n: number, o: any) => n + (o.total_inr || 0), 0))}</b></div>
              </div>
            </Card>
          )}
          <Card title="Orders">
            <div className="space-y-2">{data.orders.map((o: any) => (
              <div key={o.order_ref} className="rounded-lg border border-slate-100 p-2 text-xs">
                <div className="flex justify-between font-medium"><span>#{o.order_ref}</span><span>{inr(o.total_inr)} · {o.payment_method.toUpperCase()}</span></div>
                <div className="text-slate-500">{o.line_items.map((l: any) => l.title).join(', ')}</div>
                <div className="mt-0.5 flex gap-1"><Badge>{humanize(o.status)}</Badge>{o.courier && <Badge>{o.courier}</Badge>}</div>
              </div>))}</div>
          </Card>
          <Card title="Tickets">
            <div className="space-y-1.5">{data.tickets.map((t: any) => <Link key={t.id} to={`/tickets/${t.id}`} className="block text-sm text-brand-700 hover:underline">{tref(t.number)} · {t.summary}</Link>)}</div>
          </Card>
        </div>
      </div>
    </>
  )
}

function Journey({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ['journey', id], queryFn: () => api(`/v1/customers/${id}/journey`) })
  if (!data) return null
  const empty = !data.lead && !data.calls.length && !data.proactive.length && !data.campaigns.length
  return (
    <Card title="Journey" action={data.lead && <Link to={`/leads/${data.lead.id}`} className="text-xs font-medium text-brand-700 underline">Lead →</Link>}>
      {empty ? <div className="text-xs text-slate-400">Support only so far: no lead, calls or campaigns.</div> : (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-1 text-[11px] text-slate-500">
            {['Find', 'Talk', 'Buy', 'Support', 'Grow'].map((s, i) => <span key={s} className="flex items-center gap-1">{i > 0 && <span className="text-slate-300">→</span>}{s}</span>)}
          </div>
          {data.lead && (
            <div className="flex items-center gap-2"><Badge tone={stageTone(data.lead.stage)}>{humanize(data.lead.stage)}</Badge><ScorePill score={data.lead.score} />
              <span className="truncate text-xs text-slate-500">{data.lead.interest}</span></div>
          )}
          {data.lead_events.slice(0, 6).map((e: any) => <div key={e.id} className="flex gap-2 text-xs"><span className="w-24 shrink-0 text-slate-400">{time(e.created_at)}</span><span className="text-slate-700">{e.summary}</span></div>)}
          {data.calls.map((c: any) => (
            <Link key={c.id} to={`/calls?id=${c.id}`} className="block rounded-lg border border-slate-100 px-2 py-1.5 text-xs hover:bg-slate-50">📞 {humanize(c.direction)} call · {humanize(c.outcome || c.status)}{c.summary ? ` — ${c.summary}` : ''}</Link>
          ))}
          {data.proactive.map((p: any, i: number) => <div key={i} className="text-xs text-slate-600">🔔 {p.summary} · {ago(p.created_at)}</div>)}
          {data.campaigns.map((c: any, i: number) => <div key={i} className="text-xs text-slate-600">📣 Campaign “{c.name}” · {c.status} · {ago(c.created_at)}</div>)}
        </div>
      )}
    </Card>
  )
}

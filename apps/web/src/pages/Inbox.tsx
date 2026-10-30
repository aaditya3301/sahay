import { useQuery } from '@tanstack/react-query'
import { Bot, Inbox as InboxIcon, Search, ShieldAlert, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Card, ChannelIcon, channelName, Empty, FrustrationBar, inputCls, PageHeader, SentimentChip, Spinner, StatusBadge, Tabs, UrgencyBadge } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize } from '../lib/format'

export default function InboxPage() {
  const tref = useTicketRef()
  const [status, setStatus] = useState('active')
  const [q, setQ] = useState('')
  const { data, isLoading } = useQuery({ queryKey: ['tickets', status, q], queryFn: () => api(`/v1/tickets?status=${status}&q=${encodeURIComponent(q)}`), refetchInterval: 3000 })
  return (
    <>
      <PageHeader title="Inbox" subtitle="Every channel in one queue, sorted by urgency and customer frustration."
        actions={<div className="relative"><Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" /><input className={inputCls + ' w-64 pl-8'} placeholder="Search tickets, customers, #number" value={q} onChange={(e) => setQ(e.target.value)} /></div>} />
      <Card pad={false}>
        <div className="px-3 pt-1">
          <Tabs value={status} onChange={setStatus} tabs={[
            { id: 'active', label: 'Active' }, { id: 'escalated', label: 'Needs you' }, { id: 'pending_approval', label: 'Needs approval' },
            { id: 'resolved', label: 'Resolved' }, { id: 'all', label: 'All' }]} />
        </div>
        {isLoading ? <Spinner /> : !data?.length ? (
          <Empty icon={<InboxIcon className="h-10 w-10" />} title="Nothing here">Send a message from the Customer simulator or the demo storefront to see Sahay work.</Empty>
        ) : (
          <div className="divide-y divide-slate-100">
            {data.map((t: any) => (
              <Link key={t.id} to={`/tickets/${t.id}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-4 px-4 py-3 hover:bg-slate-50">
                <div className="flex w-9 flex-col items-center gap-1">
                  <ChannelIcon channel={t.channel} className="h-4 w-4" />
                  <span className="text-[10px] text-slate-400">{tref(t.number)}</span>
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-slate-800">{t.customer_name || 'Unknown customer'}</span>
                    {t.vip && <Badge tone="violet">VIP</Badge>}
                    <span className="text-xs text-slate-400">· {channelName(t.channel)} · {ago(t.updated_at)}</span>
                  </div>
                  <div className="truncate text-sm text-slate-700">{t.summary || t.subject}</div>
                  <div className="truncate text-xs text-slate-400">{t.last_message}</div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={t.status} />
                    <UrgencyBadge urgency={t.urgency} />
                    {t.intent && <Badge>{humanize(t.intent)}</Badge>}
                    <SentimentChip value={t.sentiment} />
                    {t.resolution_type === 'ai_auto' && <Badge tone="brand"><Bot className="h-3 w-3" />Resolved by AI</Badge>}
                    {t.resolution_type === 'ai_assisted' && <Badge tone="brand"><Sparkles className="h-3 w-3" />AI-assisted</Badge>}
                    {t.injection_suspected && <Badge tone="red"><ShieldAlert className="h-3 w-3" />Injection blocked</Badge>}
                    {t.sensitive && <Badge tone="red">Sensitive</Badge>}
                    {t.pending > 0 && <Badge tone="amber">{t.pending} pending</Badge>}
                    {t.order_refs?.map((r: string) => <Badge key={r}>#{r}</Badge>)}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1.5">
                  <FrustrationBar value={t.frustration_score} />
                  <span className="text-[10px] uppercase tracking-wide text-slate-400">priority {Math.round(t.priority_score * 100)}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </Card>
    </>
  )
}

import { useQuery } from '@tanstack/react-query'
import { Bot, Inbox as InboxIcon, Search, ShieldAlert, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, ChannelIcon, channelName, Empty, FrustrationBar, inputCls, PageHeader, SentimentChip, Spinner, StatusBadge, Tabs, UrgencyBadge } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize } from '../lib/format'

export default function InboxPage() {
  const tref = useTicketRef()
  const [status, setStatus] = useState('active')
  const [q, setQ] = useState('')
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['tickets', status, q], queryFn: () => api(`/v1/tickets?status=${status}&q=${encodeURIComponent(q)}`), refetchInterval: 3000 })
  const firstView = !q && (status === 'active' || status === 'all')
  return <>
    <PageHeader title="Inbox" subtitle="All your customer conversations."
      actions={<div className="inbox-search"><Search size={16} aria-hidden="true" /><input className={inputCls} aria-label="Search conversations" placeholder="Search conversations…" title="Search tickets, customers or ticket number" value={q} onChange={(e) => setQ(e.target.value)} /></div>} />
    <Card pad={false} className="inbox-panel">
      <div className="inbox-queue-bar">
        <Tabs value={status} onChange={setStatus} tabs={[
          { id: 'active', label: 'Active' }, { id: 'escalated', label: 'Needs you' }, { id: 'pending_approval', label: 'Needs approval' },
          { id: 'resolved', label: 'Resolved' }, { id: 'all', label: 'All' }]} />
      </div>
      {isLoading ? <Spinner /> : isError ? <div className="inbox-error" role="alert"><p>We couldn’t load your conversations.</p><Button variant="secondary" onClick={() => { void refetch() }}>Try again</Button></div> : !data?.length ? (
        <Empty icon={<InboxIcon />} title={q ? 'No matching conversations' : firstView ? 'Your inbox is ready' : 'You’re all caught up'}>
          <p>{q ? 'Try a different name or ticket number.' : firstView ? 'Customer messages will appear here.' : 'No conversations in this view.'}</p>
        </Empty>
      ) : <div>{data.map((t: any) => <article key={t.id} className="inbox-conversation">
        <Link to={`/tickets/${t.id}`} className="inbox-conversation-main">
          <div className="inbox-channel" title={channelName(t.channel)}><ChannelIcon channel={t.channel} className="h-4 w-4" /></div>
          <div className="min-w-0">
            <div className="inbox-conversation-head"><span className="inbox-customer">{t.customer_name || 'Unknown customer'}</span>{t.vip && <Badge tone="blue">VIP</Badge>}<span className="inbox-time">{ago(t.updated_at)}</span></div>
            <p className="inbox-subject">{t.summary || t.subject}</p>
            <p className="inbox-message">{t.last_message}</p>
            <div className="inbox-status">
              <StatusBadge status={t.status} />
              {t.injection_suspected && <Badge tone="red"><ShieldAlert className="h-3 w-3" />Injection blocked</Badge>}
              {t.sensitive && <Badge tone="red">Sensitive</Badge>}
              {t.pending > 0 && <Badge tone="amber">{t.pending} pending</Badge>}
            </div>
          </div>
          <div className="inbox-status-side"><UrgencyBadge urgency={t.urgency} /></div>
        </Link>
        <details className="inbox-conversation-details">
          <summary>Conversation details<span className="sr-only"> for {t.customer_name || 'Unknown customer'}, {tref(t.number)}</span></summary>
          <div>
            <span>{tref(t.number)} · {channelName(t.channel)}</span>
            {t.intent && <Badge>{humanize(t.intent)}</Badge>}
            <SentimentChip value={t.sentiment} />
            <span>Frustration</span><FrustrationBar value={t.frustration_score} />
            <span>Priority {Math.round(t.priority_score * 100)}</span>
            {t.resolution_type === 'ai_auto' && <Badge tone="brand"><Bot className="h-3 w-3" />Resolved by AI</Badge>}
            {t.resolution_type === 'ai_assisted' && <Badge tone="brand"><Sparkles className="h-3 w-3" />AI-assisted</Badge>}
            {t.order_refs?.map((r: string) => <Badge key={r}>#{r}</Badge>)}
          </div>
        </details>
      </article>)}</div>}
    </Card>
  </>
}

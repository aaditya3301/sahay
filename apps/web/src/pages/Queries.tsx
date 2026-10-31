import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { Badge, Button, Card, ChannelIcon, Empty, inputCls, PageHeader, Spinner, StatusBadge, Tabs } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { ago, humanize } from '../lib/format'

// One page per channel a query can come in on; `icon` is the channel name ChannelIcon knows
const QUERY_CHANNELS: Record<string, { label: string; title: string; subtitle: string; icon: string; empty: string }> = {
  email: { label: 'Email', title: 'Email queries', subtitle: 'What customers asked by email, and the address each one came from.', icon: 'email', empty: 'Emails customers send you will appear here.' },
  phone: { label: 'Phone calls', title: 'Phone call queries', subtitle: 'Calls customers made to your agent, and the number they called from.', icon: 'voice', empty: 'Calls customers make to your agent will appear here.' },
  chat: { label: 'Chat', title: 'Chat queries', subtitle: 'Questions asked in your website chat, and who asked them.', icon: 'webchat', empty: 'Questions from your website chat will appear here.' },
  telegram: { label: 'Telegram', title: 'Telegram queries', subtitle: 'Messages sent to your Telegram bot, and the account they came from.', icon: 'telegram', empty: 'Messages to your Telegram bot will appear here.' },
}

const pagePath = (url?: string | null) => { try { return url ? new URL(url).pathname : '' } catch { return '' } }

export default function QueriesPage() {
  const { channel = '' } = useParams()
  const tref = useTicketRef()
  const [status, setStatus] = useState('all')
  const [q, setQ] = useState('')
  const meta = QUERY_CHANNELS[channel]
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['queries', channel, status, q], enabled: !!meta, refetchInterval: 5000,
    queryFn: () => api(`/v1/queries?channel=${channel}&status=${status}&q=${encodeURIComponent(q)}`),
  })
  if (!meta) return <Navigate to="/queries/email" replace />
  return <>
    <PageHeader title={meta.title} subtitle={meta.subtitle}
      actions={<div className="inbox-search"><Search size={16} aria-hidden="true" /><input className={inputCls} aria-label={`Search ${meta.label.toLowerCase()} queries`} placeholder="Search by name, address or words…" value={q} onChange={(e) => setQ(e.target.value)} /></div>} />
    <Card pad={false} className="inbox-panel">
      <div className="inbox-queue-bar">
        <Tabs value={status} onChange={setStatus} tabs={[{ id: 'all', label: 'All' }, { id: 'open', label: 'Open' }, { id: 'resolved', label: 'Resolved' }]} />
      </div>
      {isLoading ? <Spinner /> : isError ? <div className="inbox-error" role="alert"><p>We couldn’t load these queries.</p><Button variant="secondary" onClick={() => { void refetch() }}>Try again</Button></div> : !data?.length ? (
        <Empty icon={<ChannelIcon channel={meta.icon} className="h-6 w-6" />} title={q ? 'No matching queries' : status === 'all' ? 'No queries yet' : 'Nothing in this view'}>
          <p>{q ? 'Try a different name, address or word.' : meta.empty}</p>
        </Empty>
      ) : <div>{data.map((r: any) => {
        const page = pagePath(r.origin?.page)
        const said = r.query || r.call_summary || (channel === 'phone' ? 'Nothing was said on this call yet.' : '')
        return <article key={r.id} className="inbox-conversation">
          <Link to={`/tickets/${r.id}`} className="inbox-conversation-main">
            <div className="inbox-channel"><ChannelIcon channel={meta.icon} className="h-4 w-4" /></div>
            <div className="min-w-0">
              <div className="inbox-conversation-head">
                <span className="inbox-customer" title={r.origin?.label}>{r.origin?.label}</span>
                {r.customer_name && r.customer_name !== r.origin?.label && <span className="query-who">{r.customer_name}</span>}
                <span className="inbox-time" title={new Date(r.asked_at).toLocaleString()}>{ago(r.asked_at)}</span>
              </div>
              <p className="query-origin">{[r.origin?.detail, page && `Asked on ${page}`].filter(Boolean).join(' · ')}</p>
              <p className="query-text">{said}</p>
              <div className="inbox-status">
                <StatusBadge status={r.status} />
                <Badge>{tref(r.number)}</Badge>
                {r.intent && <Badge>{humanize(r.intent)}</Badge>}
                {r.message_count > 1 && <Badge>{r.message_count} messages</Badge>}
              </div>
            </div>
          </Link>
        </article>
      })}</div>}
    </Card>
  </>
}

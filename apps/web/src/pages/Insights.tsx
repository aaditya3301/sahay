import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Bot, Clock, IndianRupee, Smile, Sparkles, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Badge, Button, Card, FrustrationBar, PageHeader, Spinner, Stat, Tabs } from '../components/ui'
import { useTicketRef } from '../lib/useTicketRef'
import { api } from '../lib/api'
import { humanize, time } from '../lib/format'

// Chart roles from the dataviz reference palette (light)
const C = { s1: '#226192', s2: '#ef8557', grid: '#e8e5de', axis: '#58646a', text: '#17394e' }
const axisProps = { stroke: C.axis, fontSize: 11, tickLine: false, axisLine: { stroke: C.grid } }

function Tip({ active, payload, label, fmt }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium text-slate-700">{label}</div>
      {payload.map((p: any) => <div key={p.dataKey} className="flex items-center gap-2 text-slate-600"><span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />{p.name}: <b className="text-slate-800">{fmt ? fmt(p.value) : p.value}</b></div>)}
    </div>
  )
}

export default function Insights() {
  const tref = useTicketRef()
  const [days, setDays] = useState('14')
  const { data: m, isLoading } = useQuery({ queryKey: ['metrics', days], queryFn: () => api(`/v1/insights/metrics?days=${days}`) })
  if (isLoading || !m) return <Spinner />
  const byDay = m.by_day.map((d: any) => ({ ...d, label: new Date(d.day).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }), other: d.n - d.ai_auto }))
  return (
    <>
      <PageHeader title="Insights" subtitle="Customer trends and your agent’s impact."
        actions={<div className="w-72 whitespace-nowrap"><Tabs value={days} onChange={setDays} tabs={[{ id: '7', label: '7 days' }, { id: '14', label: '14 days' }, { id: '30', label: '30 days' }]} /></div>} />
      <div className="mb-4 grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Tickets" value={m.total} sub={`${m.resolved} resolved`} icon={<TrendingUp className="h-4 w-4" />} />
        <Stat label="Resolved by AI alone" value={`${m.ai_resolution_rate}%`} sub={`${m.ai_auto} fully automatic`} icon={<Bot className="h-4 w-4" />} />
        <Stat label="First response (median)" value={m.frt_median_s < 120 ? `${m.frt_median_s}s` : `${Math.round(m.frt_median_s / 60)}m`} icon={<Clock className="h-4 w-4" />} />
        <Stat label="CSAT" value={m.csat_avg ?? '—'} sub="out of 5" icon={<Smile className="h-4 w-4" />} />
        <Stat label="Negative sentiment" value={`${m.negative_pct}%`} sub={`avg ${m.sentiment_avg}`} icon={<AlertTriangle className="h-4 w-4" />} />
        <Stat label="AI cost / resolved ticket" value={`₹${m.cost_per_resolved_inr}`} sub={`₹${m.llm_cost_inr} total`} icon={<IndianRupee className="h-4 w-4" />} />
      </div>
      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <Card title="Tickets per day">
          <div className="mb-1 flex gap-3 text-xs text-slate-600">
            <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: C.s1 }} />Resolved by AI alone</span>
            <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: C.s2 }} />Other</span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={byDay} barCategoryGap="20%">
              <CartesianGrid vertical={false} stroke={C.grid} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis allowDecimals={false} {...axisProps} width={28} />
              <Tooltip content={<Tip />} cursor={{ fill: '#f1f5f9' }} />
              <Bar dataKey="ai_auto" name="Resolved by AI alone" stackId="a" fill={C.s1} stroke="#fff" strokeWidth={2} />
              <Bar dataKey="other" name="Other" stackId="a" fill={C.s2} stroke="#fff" strokeWidth={2} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Customer sentiment (daily average, −1 to +1)">
          <ResponsiveContainer width="100%" height={236}>
            <LineChart data={byDay}>
              <CartesianGrid vertical={false} stroke={C.grid} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis domain={[-1, 1]} ticks={[-1, -0.5, 0, 0.5, 1]} {...axisProps} width={32} />
              <ReferenceLine y={0} stroke={C.axis} strokeDasharray="3 3" />
              <Tooltip content={<Tip fmt={(v: number) => v.toFixed(2)} />} />
              <Line dataKey="sentiment" name="Sentiment" stroke={C.s1} strokeWidth={2} dot={{ r: 4, fill: C.s1, stroke: '#fff', strokeWidth: 2 }} activeDot={{ r: 5 }} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>
      <div className="mb-4 grid gap-4 lg:grid-cols-[1fr_380px]">
        <Card title="What customers contact you about">
          <ResponsiveContainer width="100%" height={Math.max(160, m.by_intent.length * 28)}>
            <BarChart data={m.by_intent.map((x: any) => ({ ...x, label: humanize(x.intent) }))} layout="vertical" margin={{ left: 10, right: 30 }}>
              <CartesianGrid horizontal={false} stroke={C.grid} />
              <XAxis type="number" allowDecimals={false} {...axisProps} />
              <YAxis type="category" dataKey="label" {...axisProps} width={130} />
              <Tooltip content={<Tip />} cursor={{ fill: '#f1f5f9' }} />
              <Bar dataKey="n" name="Tickets" fill={C.s1} radius={[0, 4, 4, 0]} barSize={14} label={{ position: 'right', fontSize: 11, fill: C.text }} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Customers at risk (high frustration, unresolved)">
          {!m.at_risk.length ? <div className="text-sm text-slate-400">No customers at risk right now.</div> : (
            <div className="space-y-2">{m.at_risk.map((t: any) => (
              <Link key={t.id} to={`/tickets/${t.id}`} className="block rounded-lg border border-slate-100 p-2 hover:bg-slate-50">
                <div className="flex items-center justify-between text-sm"><span className="font-medium">{t.display_name}</span><FrustrationBar value={t.frustration_score} /></div>
                <div className="truncate text-xs text-slate-500">{tref(t.number)} · {t.summary}</div>
              </Link>))}</div>
          )}
        </Card>
      </div>
      <Reports />
    </>
  )
}

function Reports() {
  const qc = useQueryClient()
  const { data: reports } = useQuery({ queryKey: ['reports'], queryFn: () => api('/v1/insights/reports') })
  const gen = useMutation({ mutationFn: () => api('/v1/insights/reports', { method: 'POST' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['reports'] }) })
  const r = reports?.[0]
  return (
    <Card className="weekly-report" title={<span className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-brand-600" />Weekly insights report</span>}
      action={<Button size="sm" onClick={() => gen.mutate()} loading={gen.isPending}>Generate report</Button>}>
      {gen.isPending && <div role="status" className="mb-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-slate-600">Finding complaint patterns and preparing your report…</div>}
      {!r ? <div className="text-sm text-slate-500">No report yet. Reports run every Monday, or you can generate one now.</div> : (
        <>
          <div className="weekly-report-period">{r.period_start} – {r.period_end}<span>Generated {time(r.created_at)}</span></div>
          <div className="weekly-report-layout">
            <div className="weekly-report-main">
              <section className="weekly-report-summary"><h3>What happened this week</h3><div className="weekly-report-narrative prose-sm"><ReactMarkdown>{r.narrative_md}</ReactMarkdown></div></section>
              {r.recommendations?.length > 0 && <section className="weekly-report-recommendations"><h3>Suggested next steps</h3><ol>{r.recommendations.map((x: any, i: number) => (
                <li key={i}><span className="weekly-report-step" aria-hidden="true">•</span><div><div className="weekly-report-recommendation-title">{x.title}<Badge className="ml-2">{humanize(x.kind)}</Badge></div><p>{x.detail}</p></div></li>))}</ol></section>}
            </div>
            <aside className="weekly-report-clusters"><h3>Recurring complaints <span>{r.clusters.length}</span></h3>
              {r.clusters.map((c: any) => (
                <details key={c.cluster_id} className="weekly-cluster">
                  <summary><span><strong>{c.label}</strong><small>{c.wow_change_pct != null ? <>{c.wow_change_pct > 0 ? '↑' : '↓'} {Math.abs(c.wow_change_pct)}% vs last week</> : 'New this week'}</small></span><span className="weekly-cluster-count">{c.size}<small>tickets</small></span></summary>
                  <div className="weekly-cluster-details"><div className="weekly-cluster-drivers">{c.drivers.map((d: any) => <Badge key={d.field} tone="orange">{humanize(d.field)}: {d.value} · {d.share_pct}%{d.lift ? ` · ${d.lift}× usual` : ''}</Badge>)}</div><p><strong>Likely cause</strong>{c.root_cause}</p></div>
                </details>
              ))}
            </aside>
          </div>
        </>
      )}
    </Card>
  )
}

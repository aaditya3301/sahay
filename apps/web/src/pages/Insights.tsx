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
const C = { s1: '#2a78d6', s2: '#eb6834', grid: '#e7e5e4', axis: '#78716c', text: '#52514e' }
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
      <PageHeader title="Insights" subtitle="What customers are telling you, and what Sahay did about it."
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
          {!m.at_risk.length ? <div className="text-sm text-slate-400">No one at risk right now 🎉</div> : (
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
    <Card title={<span className="flex items-center gap-1.5"><Sparkles className="h-4 w-4 text-brand-600" />Weekly insights report</span>}
      action={<Button size="sm" onClick={() => gen.mutate()} loading={gen.isPending}>Generate this week's report</Button>}>
      {gen.isPending && <div className="mb-3 text-xs text-slate-500">Clustering complaints, finding root causes, writing the report…</div>}
      {!r ? <div className="text-sm text-slate-400">No report yet. It runs every Monday — or generate one now.</div> : (
        <div className="grid gap-5 lg:grid-cols-[1fr_420px]">
          <div>
            <div className="mb-2 text-xs text-slate-400">{r.period_start} → {r.period_end} · generated {time(r.created_at)}</div>
            <div className="prose-sm text-sm text-slate-700"><ReactMarkdown>{r.narrative_md}</ReactMarkdown></div>
            {r.recommendations?.length > 0 && (
              <div className="mt-3 space-y-2">{r.recommendations.map((x: any, i: number) => (
                <div key={i} className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-2.5 text-sm"><div className="font-medium">{x.title} <Badge className="ml-1">{humanize(x.kind)}</Badge></div><div className="text-xs text-slate-600">{x.detail}</div></div>))}</div>
            )}
          </div>
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Recurring complaint clusters</div>
            {r.clusters.map((c: any) => (
              <div key={c.cluster_id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex items-center justify-between"><span className="text-sm font-semibold">{c.label}</span>
                  <span className="text-sm tabular-nums">{c.size} <span className="text-xs text-slate-400">tickets</span></span></div>
                <div className="mt-0.5 text-xs text-slate-500">{c.wow_change_pct != null ? <>{c.wow_change_pct > 0 ? '▲' : '▼'} {Math.abs(c.wow_change_pct)}% vs last week ({c.prev_week})</> : 'new this week'}</div>
                <div className="mt-1.5 flex flex-wrap gap-1">{c.drivers.map((d: any) => <Badge key={d.field} tone="orange">{humanize(d.field)}: {d.value} · {d.share_pct}%{d.lift ? ` · ${d.lift}× usual` : ''}</Badge>)}</div>
                <div className="mt-1.5 text-xs text-slate-600">Likely cause: {c.root_cause}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}

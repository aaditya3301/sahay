import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, HelpCircle, Scale, Wand2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Card, cx, Field, inputCls, PageHeader, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { humanize, time } from '../lib/format'

export default function Policies() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['policies'], queryFn: () => api('/v1/policies') })
  const [selected, setSelected] = useState<string | null>(null)
  const [text, setText] = useState('')
  const versions = data?.versions || []
  const active = versions.find((v: any) => v.status === 'active')
  const current = versions.find((v: any) => v.id === selected) || versions[0]
  useEffect(() => { if (active && !text) setText(active.source_text) }, [active])
  const compile = useMutation({ mutationFn: () => api('/v1/policies/compile', { body: { text } }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['policies'] }); setSelected(r.id) } })
  const activate = useMutation({ mutationFn: (id: string) => api(`/v1/policies/${id}/activate`, { method: 'POST' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['policies'] }) })
  if (isLoading) return <Spinner />
  return (
    <>
      <PageHeader title="Policies" subtitle="Write your rules in plain words. Sahay compiles them once into rules that code enforces on every action — the AI can propose, only the rules decide." />
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <div className="space-y-4">
          <Card title={<span className="flex items-center gap-1.5"><Wand2 className="h-4 w-4 text-brand-600" />Your rules, in your words</span>}>
            <textarea className={inputCls + ' leading-relaxed'} rows={12} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Replace damaged items under ₹800 automatically within 7 days of delivery. Refunds always need my approval." />
            <Button className="mt-3 w-full" onClick={() => compile.mutate()} loading={compile.isPending} disabled={!text.trim()}>Compile into rules</Button>
            {compile.isPending && <div className="mt-2 text-xs text-slate-500">Gemini is compiling your rules against the allowed facts and actions…</div>}
            {compile.error && <div className="mt-2 text-xs text-red-600">{(compile.error as Error).message}</div>}
          </Card>
          <Card title="Versions" pad={false}>
            <div className="divide-y divide-slate-100">
              {versions.map((v: any) => (
                <button key={v.id} onClick={() => setSelected(v.id)} className={cx('flex w-full items-center justify-between px-4 py-2 text-left text-sm hover:bg-slate-50', current?.id === v.id && 'bg-brand-50')}>
                  <span>v{v.version} · {v.cards.length} rules</span>
                  <span className="flex items-center gap-2 text-xs text-slate-400">{time(v.created_at)}<Badge tone={v.status === 'active' ? 'green' : v.status === 'draft' ? 'amber' : 'slate'}>{v.status}</Badge></span>
                </button>
              ))}
            </div>
          </Card>
        </div>
        {current && (
          <div className="space-y-4">
            <Card title={<span className="flex items-center gap-1.5"><Scale className="h-4 w-4 text-slate-400" />Version {current.version} — what will be enforced</span>}
              action={current.status === 'draft' ? <Button size="sm" variant="success" onClick={() => activate.mutate(current.id)} loading={activate.isPending}>Confirm & activate</Button> : <Badge tone={current.status === 'active' ? 'green' : 'slate'}>{current.status}</Badge>}>
              <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg bg-slate-50 p-2.5"><div className="text-slate-500">Max automatic value</div><div className="text-base font-semibold">₹{current.rules.limits.max_auto_value_inr}</div></div>
                <div className="rounded-lg bg-slate-50 p-2.5"><div className="text-slate-500">If no rule matches</div><div className="text-base font-semibold">{humanize(current.rules.unmatched_decision)}</div></div>
              </div>
              <div className="space-y-2">
                {current.cards.map((c: any) => (
                  <div key={c.id} className={cx('rounded-lg border p-3', c.decision === 'allow' ? 'border-emerald-200 bg-emerald-50/40' : c.decision === 'deny' ? 'border-red-200 bg-red-50/40' : 'border-amber-200 bg-amber-50/40')}>
                    <div className="text-sm font-semibold text-slate-800">{c.headline}</div>
                    <div className="mt-1 flex flex-wrap gap-1">{c.when.map((w: string) => <code key={w} className="rounded bg-white px-1.5 py-0.5 text-[11px] text-slate-700 ring-1 ring-slate-200">{w}</code>)}</div>
                    <div className="mt-1.5 text-xs italic text-slate-500">“{c.source}”</div>
                    {c.deny_hint && <div className="mt-1 text-xs text-slate-600">Offer instead: {c.deny_hint}</div>}
                  </div>
                ))}
              </div>
              {current.questions?.length > 0 && (
                <div className="mt-3 rounded-lg bg-blue-50 p-3 text-xs text-blue-900">
                  <div className="mb-1 flex items-center gap-1 font-semibold"><HelpCircle className="h-3.5 w-3.5" />Sahay has questions about your rules</div>
                  <ul className="list-disc pl-4">{current.questions.map((q: string) => <li key={q}>{q}</li>)}</ul>
                </div>
              )}
              <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                <b>Always on (platform rules you can't turn off):</b> orders must belong to the customer · amounts can't exceed the order total · prompt-injection or sensitive tickets never act automatically · reused or catalog photos go to a human · per-customer automatic action limit.
              </div>
            </Card>
            <TestBench policyId={current.id} facts={data.facts} actions={data.actions} />
          </div>
        )}
      </div>
    </>
  )
}

const PRESETS: Record<string, Record<string, any>> = {
  'Damaged item, ₹649, photo OK': { 'ticket.intent': 'damaged_item', 'action.value_inr': 649, 'order.total_inr': 649, 'order.days_since_delivery': 1, 'evidence.photo_verified': true, 'customer.fraud_score': 0, 'order.belongs_to_customer': true, 'customer.verified_for_order': true },
  'Injection: refund ₹50,000': { 'ticket.intent': 'refund_request', 'action.value_inr': 50000, 'order.total_inr': 649, 'ticket.injection_suspected': true, 'order.belongs_to_customer': true, 'customer.verified_for_order': true },
  'Someone else\'s order': { 'ticket.intent': 'address_change', 'order.belongs_to_customer': false, 'order.is_shipped': false },
  'Catalog photo (fraud)': { 'ticket.intent': 'damaged_item', 'action.value_inr': 649, 'order.total_inr': 649, 'order.days_since_delivery': 3, 'evidence.photo_verified': false, 'evidence.reused_image': true, 'customer.fraud_score': 0.6, 'order.belongs_to_customer': true, 'customer.verified_for_order': true },
}

function TestBench({ policyId, facts, actions }: { policyId: string; facts: Record<string, [string, string]>; actions: Record<string, string> }) {
  const [action, setAction] = useState('create_replacement')
  const [values, setValues] = useState<Record<string, any>>(PRESETS['Damaged item, ₹649, photo OK'])
  const test = useMutation({ mutationFn: () => api(`/v1/policies/${policyId}/test`, { body: { action, facts: values } }) })
  const key = useMemo(() => JSON.stringify([action, values, policyId]), [action, values, policyId])
  useEffect(() => { const t = setTimeout(() => test.mutate(), 250); return () => clearTimeout(t) }, [key])
  const r = test.data
  return (
    <Card title={<span className="flex items-center gap-1.5"><FlaskConical className="h-4 w-4 text-slate-400" />Test bench — try a scenario, see the decision instantly</span>}>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {Object.keys(PRESETS).map((p) => <button key={p} onClick={() => { setValues(PRESETS[p]); setAction(p.startsWith('Injection') ? 'issue_refund' : p.startsWith('Someone') ? 'update_shipping_address' : 'create_replacement') }}
          className="rounded-full border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">{p}</button>)}
      </div>
      <div className="grid gap-4 md:grid-cols-[1fr_260px]">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Action"><select className={inputCls} value={action} onChange={(e) => setAction(e.target.value)}>{Object.keys(actions).map((a) => <option key={a} value={a}>{humanize(a)}</option>)}</select></Field>
          {Object.entries(facts).map(([k, [type]]) => (
            <Field key={k} label={k}>
              {type === 'bool' ? (
                <select className={inputCls} value={values[k] === undefined ? '' : String(values[k])} onChange={(e) => setValues({ ...values, [k]: e.target.value === '' ? undefined : e.target.value === 'true' })}>
                  <option value="">—</option><option value="true">true</option><option value="false">false</option></select>
              ) : (
                <input className={inputCls} value={values[k] ?? ''} onChange={(e) => setValues({ ...values, [k]: e.target.value === '' ? undefined : type === 'number' ? Number(e.target.value) : e.target.value })} />
              )}
            </Field>
          ))}
        </div>
        <div>
          {r && (
            <div className={cx('sticky top-4 rounded-xl p-4', r.decision === 'allow' ? 'bg-emerald-50 ring-1 ring-emerald-200' : r.decision === 'deny' ? 'bg-red-50 ring-1 ring-red-200' : 'bg-amber-50 ring-1 ring-amber-200')}>
              <div className="text-xs uppercase tracking-wide text-slate-500">Decision</div>
              <div className="text-xl font-semibold">{r.decision === 'allow' ? '✅ Allowed automatically' : r.decision === 'deny' ? '⛔ Denied' : '🟡 Needs approval'}</div>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-700">{r.reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>
              {r.matched_rules?.length > 0 && <div className="mt-2 text-[11px] text-slate-500">Matched: {r.matched_rules.join(', ')}</div>}
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

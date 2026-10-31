import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, HelpCircle, Scale, Wand2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, cx, inputCls, PageHeader, Spinner } from '../components/ui'
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
      <PageHeader title="Policies" subtitle="Your rules for what Sahay can do." />
      <div className="policies-layout">
        <div className="space-y-4">
          <Card title={<span className="flex items-center gap-1.5"><Wand2 className="h-4 w-4 text-brand-600" />Your rules, in your words</span>}>
            <p className="policy-helper">Tell Sahay what it can handle automatically and when it should ask you first.</p>
            <textarea className={inputCls + ' leading-relaxed'} rows={8} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="For example: Replace damaged items under ₹800 if reported within 7 days. Ask me before issuing any refund." />
            <Button size="sm" className="mt-3" onClick={() => compile.mutate()} loading={compile.isPending} disabled={!text.trim()}><Wand2 className="h-3.5 w-3.5" />Turn into rules</Button>
            {compile.isPending && <div className="mt-2 text-xs text-slate-500">Gemini is compiling your rules against the allowed facts and actions…</div>}
            {compile.error && <div className="mt-2 text-xs text-red-600">{(compile.error as Error).message}</div>}
          </Card>
          <Card title="Versions" pad={false}>
            <div className="divide-y divide-slate-100">
              {versions.map((v: any) => (
                <button key={v.id} onClick={() => setSelected(v.id)} className={cx('policy-version', current?.id === v.id && 'is-selected')}>
                  <span><strong>Version {v.version}</strong><small>{v.cards.length} rules · {time(v.created_at)}</small></span>
                  <Badge tone={v.status === 'active' ? 'green' : v.status === 'draft' ? 'amber' : 'slate'}>{v.status}</Badge>
                </button>
              ))}
            </div>
          </Card>
        </div>
        {current && (
          <div className="space-y-4">
            <Card className="policy-enforcement" title={<span className="flex items-center gap-1.5"><Scale className="h-4 w-4 text-slate-400" />Rules Sahay will follow</span>}
              action={<div className="flex items-center gap-2">{current.status === 'draft' && <Button size="sm" variant="success" onClick={() => activate.mutate(current.id)} loading={activate.isPending}>Activate rules</Button>}<Badge tone={current.status === 'active' ? 'green' : current.status === 'draft' ? 'amber' : 'slate'}>{current.status}</Badge></div>}>
              <div className="policy-limits">
                <div><span>Automatic actions up to</span><strong>₹{current.rules.limits.max_auto_value_inr}</strong></div>
                <div><span>When no rule applies</span><strong>{humanize(current.rules.unmatched_decision)}</strong></div>
              </div>
              <div className="policy-rule-list">
                {current.cards.map((c: any) => (
                  <article key={c.id} className={cx('policy-rule', `policy-rule--${c.decision}`)}>
                    <span className="policy-rule-mark" aria-hidden="true">{c.decision === 'allow' ? <CheckCircle2 size={14} /> : c.decision === 'deny' ? <XCircle size={14} /> : <AlertCircle size={14} />}</span>
                    <div className="policy-rule-content"><div className="policy-rule-heading"><strong>{c.headline}</strong><Badge tone={c.decision === 'allow' ? 'green' : c.decision === 'deny' ? 'red' : 'amber'}>{c.decision === 'allow' ? 'Automatic' : c.decision === 'deny' ? 'Not allowed' : 'Ask first'}</Badge></div>
                      {c.source && <details className="policy-rule-source"><summary>From your instructions</summary><p>“{c.source}”</p></details>}
                      {c.deny_hint && <p className="policy-rule-alternative"><strong>Offer instead:</strong> {c.deny_hint}</p>}
                      {!!c.when?.length && <details className="policy-rule-technical"><summary>When this rule applies</summary><div>{c.when.map((w: string) => <code key={w}>{w}</code>)}</div></details>}
                    </div>
                  </article>
                ))}
              </div>
              {current.questions?.length > 0 && (
                <div className="mt-3 rounded-lg bg-blue-50 p-3 text-xs text-blue-900">
                  <div className="mb-1 flex items-center gap-1 font-semibold"><HelpCircle className="h-3.5 w-3.5" />Sahay has questions about your rules</div>
                  <ul className="list-disc pl-4">{current.questions.map((q: string) => <li key={q}>{q}</li>)}</ul>
                </div>
              )}
              <details className="policy-safety"><summary>Built-in safety checks</summary><p>Orders must belong to the customer, amounts cannot exceed the order total, sensitive requests need a person, reused or catalog photos go to a human, and automatic actions are limited per customer.</p></details>
            </Card>
          </div>
        )}
      </div>
    </>
  )
}

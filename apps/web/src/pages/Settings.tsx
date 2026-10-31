import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Button, Card, Field, inputCls, PageHeader, Spinner } from '../components/ui'
import { api } from '../lib/api'

/** Business settings form state. The Integrations page header owns the Save button, so the state lives here. */
export function useBusinessSettings() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const [s, setS] = useState<any>(null)
  // Signup stores a few extra context lines (platform, channels…) after the first line of the description. Only the first
  // line is shown for editing; the rest is kept as it is so the agent never loses that background.
  const [sells, setSells] = useState('')
  const [extra, setExtra] = useState('')
  useEffect(() => {
    if (!data) return
    setS(data.settings)
    const [first, ...rest] = String(data.settings.business_description || '').split('\n')
    setSells(first || ''); setExtra(rest.join('\n').trim())
  }, [data])
  const save = useMutation({
    mutationFn: () => api('/v1/settings', { method: 'PATCH', body: { ...s, business_description: [sells.trim(), extra].filter(Boolean).join('\n') } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['me'] }) },
  })
  return { data, s, setS, sells, setSells, save, ready: !isLoading && !!s }
}

export default function SettingsPage({ embedded = false, form }: { embedded?: boolean; form?: ReturnType<typeof useBusinessSettings> }) {
  const own = useBusinessSettings()
  const { data, s, setS, sells, setSells, save, ready } = form || own
  if (!ready) return <Spinner />
  const set = (k: string) => (e: any) => setS({ ...s, [k]: e.target.value })
  const saveButton = (
    <div className="flex items-center gap-3">
      {save.isSuccess && !save.isPending && <span role="status" className="text-xs text-emerald-700">Saved</span>}
      {save.isError && <span role="alert" className="text-xs text-red-600">{(save.error as Error).message}</span>}
      <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>Save changes</Button>
    </div>
  )
  return (
    <>
      {!embedded && <PageHeader title="Settings" subtitle={`${data.settings.name} · workspace /${data.settings.slug}`} actions={saveButton} />}
      <div className="max-w-[780px]">
        <Card title="About your business">
          <div className="space-y-5">
            <Field label="What you sell" hint="A line or two about your products. Sahay uses this to answer customers.">
              <textarea className={inputCls} rows={2} value={sells} onChange={(e) => setSells(e.target.value)} />
            </Field>
            <Field label="Brand voice" hint="How Sahay should sound. It adapts to Hindi, Hinglish or English automatically.">
              <textarea className={inputCls} rows={3} value={s.brand_voice || ''} onChange={set('brand_voice')} />
            </Field>
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Website"><input className={inputCls} value={s.website_url || ''} placeholder="yourstore.in" onChange={set('website_url')} /></Field>
              <Field label="Ticket prefix" hint={`Your tickets are numbered ${(s.ticket_prefix || 'T')}-1042.`}>
                <input className={inputCls} maxLength={5} value={s.ticket_prefix || ''} placeholder="LL"
                  onChange={(e) => setS({ ...s, ticket_prefix: e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 5) })} />
              </Field>
            </div>
          </div>
        </Card>
      </div>
    </>
  )
}

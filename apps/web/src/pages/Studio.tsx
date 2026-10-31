import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, Field, inputCls } from '../components/ui'
import { api } from '../lib/api'

// One shared copy of the agent profile: the setup wizard and Workspace → Integrations edit it through these panels.
export function useStudioProfile() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['studio'], queryFn: () => api('/v1/studio') })
  const [p, setP] = useState<any>(null)
  const [dirty, setDirty] = useState(false)
  useEffect(() => { if (data) { setP(data.profile); setDirty(false) } }, [data])
  const save = useMutation({
    mutationFn: (body: any) => api('/v1/studio', { method: 'PUT', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['studio'] }),
  })
  const update = (next: any) => { setP(next); setDirty(true) }
  const setIn = (k: string, field: string, v: any) => update({ ...p, [k]: { ...p[k], [field]: v } })
  const saveAll = (over: Record<string, any> = {}) => save.mutateAsync({ agent_name: p.agent_name, persona: p.persona, skills: p.skills, custom_tools: p.custom_tools, sales: p.sales, voice: p.voice, widget: p.widget, ...over })
  return { data, p, setP: update, setIn, saveAll, save, dirty, ready: !isLoading && !!p }
}

export function PersonaTab({ p, setP, tenant }: { p: any; setP: (v: any) => void; setIn?: (k: string, f: string, v: any) => void; tenant: any }) {
  return (
    <div className="max-w-[780px]">
      <Card title="Persona">
        <div className="space-y-3">
          <Field label="Agent name" hint="Used in chat and calls."><input className={inputCls} value={p.agent_name || ''} onChange={(e) => setP({ ...p, agent_name: e.target.value })} /></Field>
          <Field label="Persona notes" hint="What to suggest, avoid or say.">
            <textarea className={inputCls} rows={5} value={p.persona || ''} onChange={(e) => setP({ ...p, persona: e.target.value })} placeholder="e.g. Always suggest the gift box for birthdays. Never compare us with other brands." />
          </Field>
          <Field label="Brand voice (from Settings)"><div className="rounded-lg bg-slate-50 p-2.5 text-sm text-slate-600">{tenant.brand_voice || '—'}</div></Field>
          <Link to="/integrations?tab=business" className="text-xs font-medium text-brand-700 underline">Edit brand voice in Business settings →</Link>
        </div>
      </Card>
    </div>
  )
}

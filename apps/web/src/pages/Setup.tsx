import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Check, ExternalLink, Loader2, PlugZap, Sparkles, Store } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmailPanel, TelegramPanel } from '../components/ChannelConnections'
import { Badge, Button, cx, Spinner } from '../components/ui'
import { api, auth } from '../lib/api'
import { buildTeammatePayloads, toneFromBrandVoice, toneInstructions, toneNames, type Tone } from '../lib/onboarding'
import { StoreConnect } from './Integrations'
import { useStudioProfile } from './Studio'
import './Setup.css'

const STEPS = [
  { id: 'store', label: 'Your store', title: 'Your store', sub: 'Show Sahay your products and orders. No store yet? Generate a demo one to try everything.' },
  { id: 'voice', label: 'Voice agent', title: 'Your voice agent', sub: 'Give your agent a name and a voice.' },
  { id: 'email', label: 'Email', title: 'Email', sub: 'Let customers email your agent, and let it reply from your address.' },
  { id: 'telegram', label: 'Telegram', title: 'Telegram', sub: 'Customers can message your agent on Telegram, any time of day.' },
  { id: 'finish', label: 'Finish', title: 'Review and finish', sub: 'Here is what you set up. You can change any of it later in Workspace → Integrations.' },
]

export default function Setup() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const tenant = auth.get()?.tenant
  const { data: me } = useQuery({ queryKey: ['me', tenant], queryFn: () => api('/v1/me') })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const studio = useStudioProfile()
  const [storeMode, setStoreMode] = useState<'demo' | 'own' | null>(null)
  const [ownSaved, setOwnSaved] = useState(false)
  const key: string | undefined = me?.widget_key

  const demo = useMutation({ mutationFn: () => api('/v1/setup/demo-store', { method: 'POST' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['setup-products'] }) })
  const products = useQuery({
    queryKey: ['setup-products', key], enabled: !!key,
    queryFn: () => fetch(`/v1/widget/products?key=${encodeURIComponent(key!)}`).then((r) => (r.ok ? r.json() : [])),
    refetchInterval: (q) => { const d = q.state.data as any[] | undefined; return d?.length && d.some((p) => !p.image) ? 4000 : false },
  })
  const hasStore = demo.isSuccess || (products.data?.length ?? 0) > 0
  const photosPending = hasStore && (products.data || []).some((p: any) => !p.image)
  // The wizard stays mounted between steps, so generation keeps running while the user moves on
  const demoStatus: 'idle' | 'working' | 'photos' | 'ready' | 'error' = demo.isPending ? 'working' : demo.isError ? 'error' : photosPending ? 'photos' : hasStore ? 'ready' : 'idle'
  const finish = useMutation({
    mutationFn: () => api('/v1/settings', { method: 'PATCH', body: { setup_done: true } }),
    onSuccess: () => { qc.setQueryData(['me', tenant], (old: any) => ({ ...old, setup_done: true })); nav('/inbox', { replace: true }) },
    onError: (e: Error) => setError(e.message),
  })

  const go = (next: number) => { setError(''); setStep(Math.max(0, Math.min(STEPS.length - 1, next))); window.scrollTo({ top: 0 }) }
  const [mate, setMate] = useState<{ agentName: string; tone: Tone; instructions: string } | null>(null)
  const teammate = mate || (studio.ready && settings ? {
    agentName: studio.p.agent_name || 'Sahay', tone: toneFromBrandVoice(settings.settings?.brand_voice),
    instructions: (studio.p.persona || '').replace(Object.values(toneInstructions).find((t) => (studio.p.persona || '').startsWith(t)) || '', '').trim(),
  } : null)
  const saveVoiceAndNext = async () => {
    if (!teammate) return
    if (!teammate.agentName.trim()) { setError('Give your agent a name.'); return }
    setError('')
    try {
      const out = buildTeammatePayloads(teammate, settings?.settings?.name || me?.tenants?.find((t: any) => t.id === me?.tenant_id)?.name || 'our store')
      await api('/v1/settings', { method: 'PATCH', body: out.settings })
      await studio.saveAll(out.studio)
      qc.invalidateQueries({ queryKey: ['settings'] })
      go(step + 1)
    } catch (e: any) { setError(e.message || 'Could not save your agent’s details') }
  }
  const cur = STEPS[step]
  const storeReady = storeMode === 'demo' ? hasStore || demo.isPending : storeMode === 'own'
  const integrations = settings?.integrations || []

  return (
    <div className="sahay-dashboard sahay-setup-root">
      <header className="setup-top">
        <span className="setup-wordmark"><img src="/sahay-mark.svg" alt="" width="30" height="30" /><span>sahay<span>.</span></span></span>
        <span className="setup-top-right">
          {storeMode === 'demo' && demoStatus !== 'idle' && (
            <span className={cx('setup-bg-status', `is-${demoStatus}`)} role="status" aria-live="polite">
              {demoStatus === 'working' || demoStatus === 'photos' ? <Loader2 size={14} className="setup-spin" aria-hidden="true" /> : demoStatus === 'ready' ? <Check size={14} aria-hidden="true" /> : null}
              {demoStatus === 'working' ? 'Generating your demo store…' : demoStatus === 'photos' ? 'Making product photos…' : demoStatus === 'ready' ? 'Demo store ready' : 'Demo store failed'}
              {demoStatus === 'error' && <button type="button" onClick={() => demo.mutate()}>Retry</button>}
            </span>
          )}
          <span className="setup-count">Step {step + 1} of {STEPS.length}</span>
        </span>
      </header>
      <nav className="setup-steps" aria-label="Setup progress"><ol>
        {STEPS.map((s, i) => (
          <li key={s.id} className={cx(i === step && 'is-current', i < step && 'is-done')} aria-current={i === step ? 'step' : undefined}>
            <button type="button" onClick={() => i < step && go(i)} disabled={i >= step} aria-label={i < step ? `Back to ${s.label}` : s.label}>
              <span className="setup-dot">{i < step ? <Check size={13} aria-hidden="true" /> : i + 1}</span><span className="setup-step-label">{s.label}</span>
            </button>
          </li>
        ))}
      </ol></nav>

      <main className={cx('setup-card', step === 1 && 'setup-card--wide')}>
        <div className="setup-intro-block"><h1>{cur.title}</h1><p>{cur.sub}</p></div>

        {step === 0 && <div className="setup-store">
          <div className="setup-choices">
            <button type="button" className={cx('setup-choice', storeMode === 'demo' && 'is-selected')} onClick={() => setStoreMode('demo')} aria-pressed={storeMode === 'demo'}>
              <Sparkles size={22} aria-hidden="true" /><strong>Generate a demo store</strong>
              <span>Sahay writes a sample catalogue for your business and builds a storefront with your chat on it. Nothing to connect.</span>
            </button>
            <button type="button" className={cx('setup-choice', storeMode === 'own' && 'is-selected')} onClick={() => setStoreMode('own')} aria-pressed={storeMode === 'own'}>
              <Store size={22} aria-hidden="true" /><strong>Connect my own website</strong>
              <span>Shopify, WooCommerce or your own site. Sahay looks up real products and orders.</span>
            </button>
          </div>

          {storeMode === 'demo' && <div className="setup-panel">
            {!hasStore && <>
              <p>We’ll create about eight products from what you told us, with photos. It takes around half a minute.</p>
              {!demo.isPending && <Button onClick={() => demo.mutate()}><Sparkles className="h-4 w-4" />Generate demo store</Button>}
              {demo.isPending && <div className="setup-working" role="status">
                <Loader2 size={20} className="setup-spin" aria-hidden="true" />
                <div><strong>Generating your demo store…</strong><span>Writing the catalogue first, then the product photos. You don’t have to wait: click Next and carry on. It keeps building in the background.</span></div>
              </div>}
              {demo.isError && <p role="alert" className="setup-error">{(demo.error as Error).message} <button type="button" className="setup-link" onClick={() => demo.mutate()}>Try again</button></p>}
            </>}
            {hasStore && <>
              <div className="setup-ready"><Badge tone={photosPending ? 'slate' : 'green'}>{photosPending ? 'Making product photos…' : 'Demo store ready'}</Badge><span>{products.data?.length || demo.data?.products || 0} products</span>
                {key && <a href={`/store/${key}`} target="_blank" rel="noreferrer" className="setup-link">Open your demo store <ExternalLink size={14} aria-hidden="true" /></a>}</div>
              <ul className="setup-products" aria-label="Demo products">
                {(products.data || []).map((p: any) => (
                  <li key={p.sku}><span className="setup-product-img">{p.image ? <img src={p.image} alt="" loading="lazy" /> : <Loader2 size={18} className="setup-spin" aria-label="Photo loading" />}</span>
                    <span className="setup-product-name">{p.title}</span><span className="setup-product-price">₹{Number(p.price_inr).toLocaleString('en-IN')}</span></li>
                ))}
              </ul>
              <Button variant="secondary" size="sm" onClick={() => demo.mutate()} loading={demo.isPending}>Generate a different set</Button>
            </>}
          </div>}

          {storeMode === 'own' && <div className="setup-panel">
            <StoreConnect hideDemo compact onSaved={() => setOwnSaved(true)} />
            <p className="setup-hint">{ownSaved ? 'Saved. You can continue.' : 'You can also save this later in Workspace → Integrations, and continue for now.'}</p>
          </div>}
        </div>}

        {step === 1 && (teammate ? <TeammateForm value={teammate} onPatch={(patch) => setMate((prev) => ({ ...(prev || teammate), ...patch }))} /> : <Spinner />)}
        {step === 2 && (settings ? <EmailPanel integrations={integrations} platformEmail={settings.platform_email} open /> : <Spinner />)}
        {step === 3 && <TelegramPanel open />}
        {step === 4 && <Review onEdit={go} voice={teammate} storeMode={storeMode} products={products.data?.length || 0} demoStatus={demoStatus} onRetry={() => demo.mutate()} />}

        {error && <p role="alert" className="setup-error">{error}</p>}
        <div className="setup-actions">
          {step > 0 ? <Button variant="ghost" onClick={() => go(step - 1)}><ArrowLeft className="h-4 w-4" />Back</Button> : <span />}
          <div className="setup-actions-right">
            {[2, 3].includes(step) && <Button variant="ghost" onClick={() => go(step + 1)}>Skip for now</Button>}
            {step === 0 && <Button onClick={() => go(1)} disabled={!storeReady}>Next<ArrowRight className="h-4 w-4" /></Button>}
            {step === 1 && <Button onClick={saveVoiceAndNext} loading={studio.save.isPending} disabled={!teammate}>Save and continue<ArrowRight className="h-4 w-4" /></Button>}
            {[2, 3].includes(step) && <Button onClick={() => go(step + 1)}>Next<ArrowRight className="h-4 w-4" /></Button>}
            {step === 4 && <Button onClick={() => finish.mutate()} loading={finish.isPending}><PlugZap className="h-4 w-4" />Finish setup</Button>}
          </div>
        </div>
        {step === 0 && !storeReady && <p className="setup-hint setup-hint--end">{storeMode === 'demo' ? 'Generate the demo store to continue.' : 'Choose one option above to continue.'}</p>}
      </main>
    </div>
  )
}

const tone = (s?: string) => (s === 'green' ? 'green' : s === 'red' ? 'red' : 'slate')

function TeammateForm({ value, onPatch }: { value: { agentName: string; tone: Tone; instructions: string }; onPatch: (patch: Partial<{ agentName: string; tone: Tone; instructions: string }>) => void }) {
  const set = onPatch
  const chips = <T extends string>(label: string, options: Record<string, string>, current: T, pick: (v: T) => void) => (
    <div className="setup-field" role="group" aria-label={label}>
      <span className="setup-label">{label}</span>
      <div className="setup-chips">{Object.entries(options).map(([id, text]) => (
        <button key={id} type="button" className={cx(current === id && 'is-selected')} aria-pressed={current === id} onClick={() => pick(id as T)}>{text}{current === id && <Check size={14} aria-hidden="true" />}</button>))}</div>
    </div>
  )
  return (
    <div className="setup-teammate">
      <label className="setup-field"><span className="setup-label">Your agent’s name</span>
        <input className="setup-input" value={value.agentName} maxLength={40} onChange={(e) => set({ agentName: e.target.value })} /></label>
      {chips<Tone>('How should your agent sound?', toneNames, value.tone, (tone) => set({ tone }))}
      <label className="setup-field"><span className="setup-label">Any service guidelines? <em>Optional</em></span>
        <textarea className="setup-input" rows={3} maxLength={1200} value={value.instructions} onChange={(e) => set({ instructions: e.target.value })} />
        <span className="setup-hint">Set order and refund rules later in Policies.</span></label>
    </div>
  )
}

function Review({ onEdit, voice, storeMode, products, demoStatus, onRetry }: { onEdit: (step: number) => void; voice?: any; storeMode: 'demo' | 'own' | null; products: number; demoStatus: string; onRetry: () => void }) {
  const { data, isFetching, refetch } = useQuery({ queryKey: ['integration-health'], queryFn: () => api('/v1/integrations/health'), staleTime: 0 })
  const check = (k: string) => (data?.checks || []).find((c: any) => c.key === k)
  const { data: tg } = useQuery({ queryKey: ['telegram'], queryFn: () => api('/v1/telegram'), retry: false })
  const rows: { step: number; label: string; status: string; text: ReactNode }[] = [
    { step: 0, label: 'Store', status: storeMode === 'demo' && demoStatus === 'working' ? 'amber' : storeMode === 'demo' && demoStatus === 'error' ? 'red' : check('store')?.status || 'amber',
      text: storeMode === 'demo' && demoStatus === 'working' ? 'Still generating your demo store. It finishes in the background.'
        : storeMode === 'demo' && demoStatus === 'error' ? 'The demo store couldn’t be generated. Edit to try again.'
        : storeMode === 'demo' && demoStatus === 'photos' ? `Demo store with ${products} products. Photos are still being made.`
        : storeMode === 'demo' ? `Demo store with ${products} products` : storeMode === 'own' ? (check('store')?.detail || 'Your own website') : (check('store')?.detail || 'Not set up') },
    { step: 1, label: 'Voice agent', status: voice ? 'green' : 'amber',
      text: voice ? `${voice.agentName} · ${toneNames[voice.tone as Tone]}` : 'Not set up' },
    { step: 2, label: 'Email', status: 'green', text: 'Sahay sends and receives your email for you' },
    { step: 3, label: 'Telegram', status: tg?.enabled ? 'green' : 'amber', text: tg?.enabled ? 'Agent bot configured' : 'Not set up' },
  ]
  return (
    <div className="setup-review">
      {rows.map((r) => (
        <div key={r.label} className="setup-review-row">
          <span className={cx('setup-review-dot', `is-${r.status}`)} aria-hidden="true" />
          <div><strong>{r.label}</strong><span>{r.text}</span></div>
          <Badge tone={tone(r.status)}>{r.step === 0 && demoStatus === 'working' ? 'In progress' : r.status === 'green' ? 'Ready' : r.status === 'red' ? 'Needs attention' : 'Not connected'}</Badge>
          <Button variant="ghost" size="sm" onClick={() => (r.step === 0 && demoStatus === 'error' ? onRetry() : onEdit(r.step))}>{r.step === 0 && demoStatus === 'error' ? 'Retry' : 'Edit'}</Button>
        </div>
      ))}
      <button type="button" className="setup-link" onClick={() => refetch()}>{isFetching ? 'Checking connections…' : 'Refresh status'}</button>
      <p className="setup-hint">Not-connected items are fine for now. Your agent works with whatever is ready, and you can finish the rest later.</p>
    </div>
  )
}

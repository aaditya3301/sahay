import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, auth, type Tenant } from '../lib/api'
import {
  agentGreeting, buildOnboardingPayloads, channelNames, goalNames, languageNames, newOnboardingDraft,
  platformNames, toneNames, validateOnboardingStep,
  type FieldErrors, type Goal, type OnboardingDraft, type OnboardingStep, type ReplyMode, type Tone,
} from '../lib/onboarding'
import './Auth.css'

type AuthResponse = { token: string; tenants: Tenant[]; user: { name: string; email: string } }
const stages = ['Business', 'Customers', 'Your agent', 'Account']
const stageCopy = [
  { title: 'Your business', sub: 'What should Sahay know?' },
  { title: 'Your customers', sub: 'Where and how they reach you.' },
  { title: 'Your teammate', sub: 'Give your agent a voice.' },
  { title: 'Your account', sub: 'One last hello.' },
]

function sessionFrom(response: AuthResponse) {
  auth.set({ token: response.token, tenants: response.tenants, tenant: response.tenants[0]?.id, user: response.user })
}

function readableError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return 'An account already uses this email. Sign in, or try a different address.'
  if (error instanceof ApiError && error.status === 401) return 'That email and password don’t match. Please try again.'
  if ((error instanceof ApiError && error.status >= 500) || error instanceof TypeError) return 'We can’t reach Sahay right now. Please try again shortly.'
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}

function AuthLogo() {
  return <Link to="/" className="sahay-auth-logo" aria-label="Sahay home">
    <svg viewBox="0 0 36 36" aria-hidden="true" fill="none"><path d="M7 18a11 11 0 0 1 22 0v3a7 7 0 0 1-7 7h-3l-6 4v-5A11 11 0 0 1 7 18Z" fill="currentColor" /><path d="M14 17c1.5 3.2 6.5 3.2 8 0" stroke="var(--auth-ground)" strokeWidth="2.4" strokeLinecap="round" /></svg>
    <span>sahay<span>.</span></span>
  </Link>
}

function AuthShell({ children, signup = false, draft, showPreview = false }: { children: ReactNode; signup?: boolean; draft?: OnboardingDraft; showPreview?: boolean }) {
  useEffect(() => {
    const previous = document.title
    document.title = signup ? 'Set up your Sahay teammate' : 'Sign in to Sahay'
    return () => { document.title = previous }
  }, [signup])
  return <div className={`sahay-auth${signup ? ' sahay-auth--signup' : ''}`}>
    <div className="sahay-auth-art" aria-hidden="true" />
    <header className="sahay-auth-header"><AuthLogo /><Link className="sahay-auth-home" to="/"><ArrowLeft size={16} aria-hidden="true" /> Back to home</Link></header>
    <main className="sahay-auth-main">
      <aside className="sahay-auth-story" aria-label="Your Sahay teammate">
        <div className="sahay-auth-story-copy">
          <p className="sahay-auth-story-title">{signup ? <>Let’s make<br />it yours.</> : <>Good help.<br />Right here.</>}</p>
        </div>
        {showPreview && draft && <div className="sahay-auth-preview" aria-live="polite" aria-atomic="true"><span>A first hello</span><p>“{agentGreeting(draft)}”</p></div>}
      </aside>
      <div className="sahay-auth-card">{children}</div>
    </main>
    <footer className="sahay-auth-footer"><span>© {new Date().getFullYear()} Sahay</span></footer>
  </div>
}

function FormField({ id, label, error, hint, optional = false, children }: {
  id: string; label: string; error?: string; hint?: string; optional?: boolean; children: ReactNode
}) {
  return <div className={`sahay-auth-field${error ? ' sahay-auth-field--error' : ''}`}>
    <label htmlFor={id}>{label}{optional && <span>Optional</span>}</label>
    {children}
    {hint && <p className="sahay-auth-hint" id={`${id}-hint`}>{hint}</p>}
    {error && <p className="sahay-auth-field-error" id={`${id}-error`} role="alert">{error}</p>}
  </div>
}

function PasswordInput({ id, value, onChange, error, signup = false }: { id: string; value: string; onChange: (value: string) => void; error?: string; signup?: boolean }) {
  const [visible, setVisible] = useState(false)
  return <div className="sahay-auth-password">
    <input id={id} name="password" type={visible ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)} autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 8 : undefined}
      aria-invalid={!!error} aria-describedby={[signup && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ') || undefined} />
    <button type="button" aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={19} /> : <Eye size={19} />}</button>
  </div>
}

function ChoiceGroup({ name, label, options, values, onChange, multiple = false, hint, error }: {
  name: string; label: string; options: Record<string, string>; values: string[]; onChange: (value: string) => void; multiple?: boolean; hint?: string; error?: string
}) {
  return <fieldset className="sahay-auth-choices" aria-describedby={[hint && `${name}-hint`, error && `${name}-error`].filter(Boolean).join(' ') || undefined}>
    <legend>{label}</legend>
    <div className="sahay-auth-choice-options">{Object.entries(options).map(([value, text]) => <label key={value} className={values.includes(value) ? 'is-selected' : ''}>
      <input type={multiple ? 'checkbox' : 'radio'} name={name} value={value} checked={values.includes(value)} onChange={() => onChange(value)} aria-invalid={!!error} /><span>{text}</span>{values.includes(value) && <Check size={14} aria-hidden="true" />}
    </label>)}</div>
    {hint && <p className="sahay-auth-hint" id={`${name}-hint`}>{hint}</p>}
    {error && <p className="sahay-auth-field-error" id={`${name}-error`} role="alert">{error}</p>}
  </fieldset>
}

export default function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const form = useRef<HTMLFormElement>(null)
  const submitting = useRef(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting.current) return
    const nextErrors: typeof errors = {}
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) nextErrors.email = 'Enter your email address.'
    if (!password) nextErrors.password = 'Enter your password.'
    setErrors(nextErrors); setError('')
    if (Object.keys(nextErrors).length) { requestAnimationFrame(() => form.current?.querySelector<HTMLInputElement>('[aria-invalid="true"]')?.focus()); return }
    submitting.current = true; setLoading(true)
    try {
      const response = await api<AuthResponse>('/v1/auth/login', { body: { email: email.trim(), password } })
      sessionFrom(response)
      navigate('/inbox')
    } catch (reason) { setError(readableError(reason)) }
    finally { submitting.current = false; setLoading(false) }
  }

  return <AuthShell>
    <div className="sahay-auth-intro"><h1>Sign in</h1></div>
    <form ref={form} onSubmit={submit} noValidate className="sahay-auth-form" aria-busy={loading}>
      <fieldset className="sahay-auth-fields" disabled={loading}>
        <FormField id="login-email" label="Email address" error={errors.email}><input id="login-email" name="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required aria-invalid={!!errors.email} aria-describedby={errors.email ? 'login-email-error' : undefined} /></FormField>
        <FormField id="login-password" label="Password" error={errors.password}><PasswordInput id="login-password" value={password} onChange={setPassword} error={errors.password} /></FormField>
      </fieldset>
      {error && <p className="sahay-auth-error" role="alert">{error}</p>}
      <button className="sahay-auth-primary" type="submit" disabled={loading}>{loading ? <><Loader2 size={18} className="sahay-auth-spinner" aria-hidden="true" /> Signing in…</> : <>Sign in <ArrowRight size={18} aria-hidden="true" /></>}</button>
    </form>
    <p className="sahay-auth-switch">New here? <Link to="/signup">Create a workspace</Link></p>
  </AuthShell>
}

export function Signup() {
  const navigate = useNavigate()
  const [draft, setDraft] = useState(newOnboardingDraft)
  const [step, setStep] = useState<OnboardingStep>(0)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [accountCreated, setAccountCreated] = useState(false)
  const account = useRef<AuthResponse | null>(null)
  const submitting = useRef(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const form = useRef<HTMLFormElement>(null)
  const mounted = useRef(false)

  useEffect(() => { if (mounted.current) heading.current?.focus({ preventScroll: true }); mounted.current = true }, [step])

  const set = <K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) => {
    setDraft((previous) => ({ ...previous, [key]: value }))
    setErrors((previous) => ({ ...previous, [key]: undefined }))
  }
  const toggle = (key: 'languages' | 'channels', value: string) => { set(key, draft[key].includes(value) ? draft[key].filter((item) => item !== value) : [...draft[key], value]) }
  const goTo = (next: OnboardingStep) => { if (loading || accountCreated) return; setErrors({}); setError(''); setStep(next) }
  const describedBy = (key: keyof OnboardingDraft) => errors[key] ? `setup-${key}-error` : undefined
  const inputProps = (key: keyof OnboardingDraft) => ({ id: `setup-${key}`, name: key, 'aria-invalid': !!errors[key], 'aria-describedby': describedBy(key) })

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting.current) return
    // After account creation only preferences are retried; the password has been cleared.
    const nextErrors = account.current ? {} : validateOnboardingStep(draft, step)
    setErrors(nextErrors); setError('')
    if (Object.keys(nextErrors).length) { requestAnimationFrame(() => form.current?.querySelector<HTMLInputElement>('[aria-invalid="true"]')?.focus()); return }
    if (step < 3) { setStep((step + 1) as OnboardingStep); return }
    for (const earlier of [0, 1, 2] as OnboardingStep[]) {
      const earlierErrors = validateOnboardingStep(draft, earlier)
      if (Object.keys(earlierErrors).length) { setErrors(earlierErrors); setStep(earlier); return }
    }
    submitting.current = true; setLoading(true)
    try {
      const payloads = buildOnboardingPayloads(draft)
      if (!account.current) {
        account.current = await api<AuthResponse>('/v1/auth/signup', { body: payloads.account })
        sessionFrom(account.current)
        setAccountCreated(true)
        setDraft((previous) => ({ ...previous, password: '' }))
      }
      await api('/v1/settings', { method: 'PATCH', body: payloads.settings })
      await api('/v1/studio', { method: 'PUT', body: payloads.studio })
      navigate('/knowledge?onboarding=1')
    } catch (reason) { setError(account.current ? 'Your account is ready, but we couldn’t save all your agent preferences. Retry setup, or finish in Agent Studio.' : readableError(reason)) }
    finally { submitting.current = false; setLoading(false) }
  }

  return <AuthShell signup draft={draft} showPreview={step === 2}>
    <nav className="sahay-auth-progress" aria-label="Workspace setup progress"><ol>{stages.map((label, index) => <li key={label} className={index === step ? 'is-current' : index < step ? 'is-complete' : ''} aria-current={index === step ? 'step' : undefined}>
      {index < step && !accountCreated ? <button type="button" onClick={() => goTo(index as OnboardingStep)} disabled={loading} aria-label={`Edit ${label.toLowerCase()}`}><span className="sahay-auth-step-dot"><Check size={11} aria-hidden="true" /></span>{label}</button> : <span><span className="sahay-auth-step-dot">{index < step && <Check size={11} aria-hidden="true" />}</span>{label}</span>}
    </li>)}</ol></nav>
    <div className="sahay-auth-intro"><h1 ref={heading} tabIndex={-1}>{stageCopy[step].title}</h1><p>{stageCopy[step].sub}</p></div>
    <form ref={form} onSubmit={submit} noValidate className="sahay-auth-form sahay-auth-form--setup" aria-busy={loading}>
      <fieldset className="sahay-auth-fields" disabled={loading || accountCreated}>
        {step === 0 && <>
          <div className="sahay-auth-field-row">
            <FormField id="setup-workspace" label="Business name" error={errors.workspace}><input {...inputProps('workspace')} value={draft.workspace} onChange={(e) => set('workspace', e.target.value)} autoComplete="organization" required maxLength={120} /></FormField>
            <FormField id="setup-platform" label="Where do you sell?" error={errors.platform}><select {...inputProps('platform')} value={draft.platform} onChange={(e) => set('platform', e.target.value)} required><option value="" disabled>Choose a platform</option>{Object.entries(platformNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></FormField>
          </div>
          <FormField id="setup-website" label="Your website" optional error={errors.website}><input {...inputProps('website')} type="text" inputMode="url" autoComplete="url" placeholder="yourstore.in" value={draft.website} onChange={(e) => set('website', e.target.value)} maxLength={500} /></FormField>
          <FormField id="setup-description" label="What do you sell or offer?" error={errors.description}><textarea {...inputProps('description')} rows={3} placeholder="A little about your products or services…" value={draft.description} onChange={(e) => set('description', e.target.value)} required maxLength={1600} /></FormField>
        </>}
        {step === 1 && <>
          <FormField id="setup-audience" label="Who are your customers?" optional><input {...inputProps('audience')} placeholder="e.g. Gift shoppers or corporate teams" value={draft.audience} onChange={(e) => set('audience', e.target.value)} maxLength={300} /></FormField>
          <ChoiceGroup name="setup-goal" label="What should Sahay help with?" options={goalNames} values={[draft.goal]} onChange={(value) => set('goal', value as Goal)} error={errors.goal} />
          <ChoiceGroup name="setup-languages" label="Languages your customers use" options={languageNames} values={draft.languages} onChange={(value) => toggle('languages', value)} multiple error={errors.languages} />
          <ChoiceGroup name="setup-channels" label="Where will you use Sahay?" options={channelNames} values={draft.channels} onChange={(value) => toggle('channels', value)} multiple error={errors.channels} hint="Connect your channels after setup." />
        </>}
        {step === 2 && <>
          <FormField id="setup-agentName" label="Your agent’s name" error={errors.agentName}><input {...inputProps('agentName')} value={draft.agentName} onChange={(e) => set('agentName', e.target.value)} required maxLength={40} /></FormField>
          <ChoiceGroup name="setup-tone" label="How should your agent sound?" options={toneNames} values={[draft.tone]} onChange={(value) => set('tone', value as Tone)} error={errors.tone} />
          <FormField id="setup-instructions" label="Any service guidelines?" optional hint="Set order and refund rules later in Policies."><textarea {...inputProps('instructions')} rows={2} value={draft.instructions} onChange={(e) => set('instructions', e.target.value)} aria-describedby="setup-instructions-hint" maxLength={1200} /></FormField>
          <ChoiceGroup name="setup-replyMode" label="How should replies be handled?" options={{ draft: 'Draft for my review', auto: 'Reply automatically' }} values={[draft.replyMode]} onChange={(value) => set('replyMode', value as ReplyMode)} error={errors.replyMode} />
          <p className="sahay-auth-approval-note"><ShieldCheck size={17} aria-hidden="true" /><span>Order actions need approval until you set your rules.</span></p>
        </>}
        {step === 3 && <>
          <FormField id="setup-name" label="Your name" error={errors.name}><input {...inputProps('name')} autoComplete="name" value={draft.name} onChange={(e) => set('name', e.target.value)} required maxLength={120} /></FormField>
          <FormField id="setup-email" label="Email address" error={errors.email}><input {...inputProps('email')} type="email" autoComplete="username" value={draft.email} onChange={(e) => set('email', e.target.value)} required maxLength={254} /></FormField>
          {!accountCreated && <FormField id="setup-password" label="Create a password" error={errors.password} hint="At least 8 characters."><PasswordInput id="setup-password" signup value={draft.password} onChange={(value) => set('password', value)} error={errors.password} /></FormField>}
          <div className="sahay-auth-review" aria-label="Your setup summary">
            <div><span>Your workspace</span><strong>{draft.workspace}</strong></div>
            <div><span>Your teammate</span><strong>{draft.agentName} · {toneNames[draft.tone]}</strong></div>
            <div><span>Languages</span><strong>{draft.languages.map((value) => languageNames[value]).join(', ')}</strong></div>
            <div><span>Replies</span><strong>{draft.replyMode === 'draft' ? 'Reviewed by you' : 'Sent automatically'}</strong></div>
          </div>
        </>}
      </fieldset>
      {error && <p className="sahay-auth-error" role="alert">{error}</p>}
      <div className="sahay-auth-form-actions">
        {step > 0 && !accountCreated && <button className="sahay-auth-back" type="button" disabled={loading} onClick={() => goTo((step - 1) as OnboardingStep)}><ArrowLeft size={16} aria-hidden="true" /> Back</button>}
        <button className="sahay-auth-primary" type="submit" disabled={loading}>{loading ? <><Loader2 size={18} className="sahay-auth-spinner" aria-hidden="true" />{accountCreated ? 'Saving your agent…' : 'Creating your workspace…'}</> : <>{step < 3 ? 'Continue' : accountCreated ? 'Retry setup' : 'Create workspace'}<ArrowRight size={18} aria-hidden="true" /></>}</button>
      </div>
      {accountCreated && error && <Link className="sahay-auth-finish-later" to="/studio">Finish in Agent Studio</Link>}
    </form>
    <p className="sahay-auth-switch">Have an account? <Link to="/login">Sign in</Link></p>
  </AuthShell>
}

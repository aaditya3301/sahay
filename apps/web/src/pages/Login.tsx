import { Bot } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, Field, inputCls } from '../components/ui'
import { api, auth } from '../lib/api'

function Shell({ children, title, sub }: { children: React.ReactNode; title: string; sub: string }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-brand-50 via-white to-orange-50 p-6">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600 text-white"><Bot className="h-6 w-6" /></div>
          <div><div className="text-lg font-bold">Sahay</div><div className="text-xs text-slate-500">The AI support teammate that resolves tickets, not just answers them.</div></div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold">{title}</h1>
          <p className="mb-5 text-sm text-slate-500">{sub}</p>
          {children}
        </div>
      </div>
    </div>
  )
}

export default function Login() {
  const nav = useNavigate()
  const [email, setEmail] = useState('demo@sahay.app')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(''); setLoading(true)
    try {
      const r = await api('/v1/auth/login', { body: { email, password } })
      auth.set({ token: r.token, tenants: r.tenants, tenant: r.tenants[0]?.id, user: r.user })
      nav('/inbox')
    } catch (e: any) { setErr(e.message) } finally { setLoading(false) }
  }
  return (
    <Shell title="Sign in" sub="Welcome back. Your AI teammate has been busy.">
      <form onSubmit={submit} className="space-y-3">
        <Field label="Email"><input className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Password"><input type="password" className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)} autoFocus /></Field>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <Button type="submit" loading={loading} className="w-full">Sign in</Button>
      </form>
      <div className="mt-4 text-center text-xs text-slate-500">New brand? <Link to="/signup" className="font-medium text-brand-700">Create a workspace</Link></div>
    </Shell>
  )
}

export function Signup() {
  const nav = useNavigate()
  const [f, setF] = useState({ name: '', email: '', password: '', workspace: '', website_url: '', business_description: '' })
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(''); setLoading(true)
    try {
      const r = await api('/v1/auth/signup', { body: f })
      auth.set({ token: r.token, tenants: r.tenants, tenant: r.tenants[0]?.id, user: r.user })
      nav('/knowledge?onboarding=1')
    } catch (e: any) { setErr(e.message) } finally { setLoading(false) }
  }
  return (
    <Shell title="Create your workspace" sub="Set up Sahay for your brand in a few minutes.">
      <form onSubmit={submit} className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Your name"><input className={inputCls} value={f.name} onChange={set('name')} required /></Field>
          <Field label="Brand name"><input className={inputCls} value={f.workspace} onChange={set('workspace')} required /></Field>
        </div>
        <Field label="Email"><input className={inputCls} type="email" value={f.email} onChange={set('email')} required /></Field>
        <Field label="Password"><input className={inputCls} type="password" value={f.password} onChange={set('password')} required minLength={6} /></Field>
        <Field label="Website (optional)"><input className={inputCls} value={f.website_url} onChange={set('website_url')} placeholder="https://yourbrand.in" /></Field>
        <Field label="What do you sell?"><textarea className={inputCls} rows={2} value={f.business_description} onChange={set('business_description')} placeholder="Handmade candles, shipping across India, mostly prepaid…" /></Field>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <Button type="submit" loading={loading} className="w-full">Create workspace</Button>
      </form>
      <div className="mt-4 text-center text-xs text-slate-500">Have an account? <Link to="/login" className="font-medium text-brand-700">Sign in</Link></div>
    </Shell>
  )
}

import clsx from 'clsx'
import { AlertTriangle, CheckCircle2, Clock, Globe, Loader2, Mail, MessageCircle, MessagesSquare, Phone, ShieldAlert, UserRound, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { humanize, sentimentLabel } from '../lib/format'

export const cx = clsx

export function Card({ children, className, title, action, pad = true }: { children: ReactNode; className?: string; title?: ReactNode; action?: ReactNode; pad?: boolean }) {
  return (
    <div className={cx('rounded-xl border border-slate-200 bg-white shadow-sm', className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <div className="text-sm font-semibold text-slate-800">{title}</div>
          {action}
        </div>
      )}
      <div className={pad ? 'p-4' : ''}>{children}</div>
    </div>
  )
}

const tones: Record<string, string> = {
  slate: 'bg-slate-100 text-slate-700 ring-slate-200',
  blue: 'bg-blue-50 text-blue-700 ring-blue-200',
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200',
  orange: 'bg-orange-50 text-orange-700 ring-orange-200',
  red: 'bg-red-50 text-red-700 ring-red-200',
  violet: 'bg-violet-50 text-violet-700 ring-violet-200',
  brand: 'bg-brand-50 text-brand-700 ring-brand-100',
}

export function Badge({ children, tone = 'slate', className, title }: { children: ReactNode; tone?: string; className?: string; title?: string }) {
  return <span title={title} className={cx('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap', tones[tone] || tones.slate, className)}>{children}</span>
}

export function Button({ children, onClick, variant = 'primary', size = 'md', disabled, loading, className, type = 'button', title }: {
  children: ReactNode; onClick?: () => void; variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'; size?: 'sm' | 'md'
  disabled?: boolean; loading?: boolean; className?: string; type?: 'button' | 'submit'; title?: string
}) {
  const v = {
    primary: 'bg-brand-600 text-white hover:bg-brand-700 shadow-sm',
    secondary: 'bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50',
    ghost: 'text-slate-600 hover:bg-slate-100',
    danger: 'bg-red-600 text-white hover:bg-red-700',
    success: 'bg-emerald-600 text-white hover:bg-emerald-700',
  }[variant]
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled || loading}
      className={cx('inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3.5 py-2 text-sm', v, className)}>
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{children}
    </button>
  )
}

export function Spinner({ label }: { label?: string }) {
  return <div className="flex items-center gap-2 p-6 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />{label || 'Loading…'}</div>
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="text-slate-300">{icon}</div>
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {children && <div className="max-w-md text-xs text-slate-500">{children}</div>}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">{actions}</div>
    </div>
  )
}

export function ChannelIcon({ channel, className = 'h-3.5 w-3.5' }: { channel?: string | null; className?: string }) {
  if (channel === 'whatsapp') return <MessageCircle className={cx(className, 'text-emerald-600')} />
  if (channel === 'email') return <Mail className={cx(className, 'text-sky-600')} />
  if (channel === 'webchat') return <Globe className={cx(className, 'text-violet-600')} />
  if (channel === 'sim') return <MessagesSquare className={cx(className, 'text-slate-500')} />
  if (channel === 'voice') return <Phone className={cx(className, 'text-amber-600')} />
  return <UserRound className={cx(className, 'text-slate-400')} />
}

export const channelName = (c?: string | null) => ({ whatsapp: 'WhatsApp', email: 'Email', webchat: 'Web chat', sim: 'Simulation', voice: 'Phone call' } as any)[c || ''] || humanize(c)

// Status always ships with icon + label (never color alone)
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, ReactNode, string]> = {
    open: ['blue', <Clock className="h-3 w-3" />, 'Open'],
    pending_customer: ['slate', <Clock className="h-3 w-3" />, 'Waiting on customer'],
    pending_approval: ['amber', <AlertTriangle className="h-3 w-3" />, 'Needs approval'],
    escalated: ['red', <ShieldAlert className="h-3 w-3" />, 'Needs you'],
    resolved: ['green', <CheckCircle2 className="h-3 w-3" />, 'Resolved'],
    closed: ['slate', <XCircle className="h-3 w-3" />, 'Closed'],
  }
  const [tone, icon, label] = map[status] || ['slate', null, humanize(status)]
  return <Badge tone={tone}>{icon}{label}</Badge>
}

export function UrgencyBadge({ urgency }: { urgency?: string | null }) {
  if (!urgency) return null
  const tone = { P1: 'red', P2: 'orange', P3: 'blue', P4: 'slate' }[urgency] || 'slate'
  return <Badge tone={tone} title="Urgency">{urgency}</Badge>
}

export function SentimentChip({ value }: { value?: number | null }) {
  const s = sentimentLabel(value)
  return <Badge tone={s.tone} title={`Sentiment ${value?.toFixed(2) ?? ''}`}>{s.emoji} {s.label}</Badge>
}

export function FrustrationBar({ value }: { value?: number | null }) {
  const v = Math.round(value || 0)
  const color = v >= 70 ? 'bg-critical' : v >= 50 ? 'bg-serious' : v >= 30 ? 'bg-warning' : 'bg-good'
  return (
    <div className="flex items-center gap-1.5" title={`Frustration ${v}/100`}>
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100"><div className={cx('h-full rounded-full', color)} style={{ width: `${v}%` }} /></div>
      <span className="text-[11px] tabular-nums text-slate-500">{v}</span>
    </div>
  )
}

export function Stat({ label, value, sub, icon }: { label: string; value: ReactNode; sub?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between text-xs font-medium text-slate-500">{label}<span className="text-slate-400">{icon}</span></div>
      <div className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div className={cx('max-h-[90vh] w-full overflow-auto rounded-xl bg-white shadow-xl scroll-thin', wide ? 'max-w-3xl' : 'max-w-lg')} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-5 py-3"><div className="font-semibold">{title}</div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button></div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="flex gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={cx('-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium', value === t.id ? 'border-brand-600 text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-800')}>{t.label}</button>
      ))}
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-slate-400">{hint}</span>}
    </label>
  )
}

export const inputCls = 'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100'

export const inr = (v: number | null | undefined, paise = false) =>
  v == null ? '—' : '₹' + Math.round(paise ? v / 100 : v).toLocaleString('en-IN')

export function ago(iso?: string | null) {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export const time = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'

export const humanize = (s?: string | null) => (s || '').replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())

export function sentimentLabel(s?: number | null) {
  if (s == null) return { label: '—', emoji: '·', tone: 'slate' }
  if (s <= -0.5) return { label: 'Angry', emoji: '😡', tone: 'red' }
  if (s < -0.1) return { label: 'Unhappy', emoji: '🙁', tone: 'orange' }
  if (s < 0.3) return { label: 'Neutral', emoji: '😐', tone: 'slate' }
  return { label: 'Happy', emoji: '🙂', tone: 'green' }
}

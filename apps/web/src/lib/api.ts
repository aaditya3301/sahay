// Tiny API client: bearer token + selected workspace header.
export type Tenant = { id: string; slug: string; name: string; role: string }

const KEY = 'sahay.auth'

export const auth = {
  get(): { token: string; tenant: string; tenants: Tenant[]; user: { name: string; email: string } } | null {
    try { return JSON.parse(localStorage.getItem(KEY) || 'null') } catch { return null }
  },
  set(v: any) { localStorage.setItem(KEY, JSON.stringify(v)) },
  clear() { localStorage.removeItem(KEY) },
  setTenant(id: string) { const a = this.get(); if (a) { a.tenant = id; this.set(a) } },
}

export class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s } }

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData } = {}): Promise<T> {
  const a = auth.get()
  const headers: Record<string, string> = {}
  if (a?.token) headers.Authorization = `Bearer ${a.token}`
  if (a?.tenant) headers['X-Tenant-Id'] = a.tenant
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body) }
  else if (opts.method && opts.method !== 'GET') { headers['Content-Type'] = 'application/json'; body = '{}' } // Cloud Run's frontend requires a length on writes
  const r = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body })
  if (r.status === 401) { auth.clear(); location.href = '/login'; throw new ApiError(401, 'Signed out') }
  const text = await r.text()
  const data = text ? (() => { try { return JSON.parse(text) } catch { return text } })() : null
  if (!r.ok) throw new ApiError(r.status, (data && (data.detail || data.message)) || r.statusText)
  return data as T
}

export function mediaUrl(attachmentId: string) {
  const a = auth.get()
  return `/media/${attachmentId}?token=${encodeURIComponent(a?.token || '')}&tenant=${a?.tenant || ''}`
}

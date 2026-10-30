import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BarChart3, BookOpen, Bot, CheckSquare, FlaskConical, GraduationCap, Inbox, LogOut, Megaphone, Phone, Scale, Settings, Shield, Smartphone, Sparkles, Store, Target, Users, Plug } from 'lucide-react'
import type { ReactNode } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { cx } from './components/ui'
import { api, auth } from './lib/api'
import Approvals from './pages/Approvals'
import Calls from './pages/Calls'
import Campaigns from './pages/Campaigns'
import Customers, { CustomerDetail } from './pages/Customers'
import InboxPage from './pages/Inbox'
import Insights from './pages/Insights'
import Integrations from './pages/Integrations'
import Knowledge from './pages/Knowledge'
import Leads, { LeadDetail } from './pages/Leads'
import Learning from './pages/Learning'
import Login, { Signup } from './pages/Login'
import Policies from './pages/Policies'
import Security from './pages/Security'
import SettingsPage from './pages/Settings'
import Simulations, { SimRun } from './pages/Simulations'
import Simulator from './pages/Simulator'
import StoreDemo from './pages/StoreDemo'
import Studio from './pages/Studio'
import TicketPage from './pages/Ticket'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/store/:key" element={<StoreDemo />} />
      <Route path="/*" element={<Protected />} />
    </Routes>
  )
}

function Protected() {
  if (!auth.get()?.token) return <Navigate to="/login" replace />
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/inbox" replace />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/tickets/:id" element={<TicketPage />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/simulator" element={<Simulator />} />
        <Route path="/customers" element={<Customers />} />
        <Route path="/customers/:id" element={<CustomerDetail />} />
        <Route path="/leads" element={<Leads />} />
        <Route path="/leads/:id" element={<LeadDetail />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/calls" element={<Calls />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/policies" element={<Policies />} />
        <Route path="/simulations" element={<Simulations />} />
        <Route path="/simulations/:id" element={<SimRun />} />
        <Route path="/insights" element={<Insights />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="/security" element={<Security />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Routes>
    </Layout>
  )
}

function Layout({ children }: { children: ReactNode }) {
  const a = auth.get()!
  const qc = useQueryClient()
  const nav = useNavigate()
  const loc = useLocation()
  const { data: ov } = useQuery({ queryKey: ['overview', a.tenant], queryFn: () => api('/v1/overview'), refetchInterval: 4000 })
  const { data: me } = useQuery({ queryKey: ['me', a.tenant], queryFn: () => api('/v1/me') })
  const tenants = me?.tenants || a.tenants || []
  const current = tenants.find((t: any) => t.id === (me?.tenant_id || a.tenant))

  const groups: { label: string; items: [string, string, ReactNode, number?][] }[] = [
    { label: 'Support', items: [
      ['/inbox', 'Inbox', <Inbox />, ov?.open_tickets],
      ['/approvals', 'Approvals', <CheckSquare />, ov?.pending_approvals],
      ['/customers', 'Customers', <Users />],
      ['/simulator', 'Customer simulator', <Smartphone />],
    ] },
    { label: 'Sales', items: [
      ['/leads', 'Leads', <Target />],
      ['/campaigns', 'Campaigns', <Megaphone />],
      ['/calls', 'Calls', <Phone />],
    ] },
    { label: 'AI teammate', items: [
      ['/studio', 'Agent Studio', <Sparkles />],
      ['/knowledge', 'Knowledge', <BookOpen />, ov?.gap_suggestions],
      ['/policies', 'Policies', <Scale />],
      ['/simulations', 'Simulations', <FlaskConical />],
      ['/learning', 'Learning', <GraduationCap />],
    ] },
    { label: 'Business', items: [
      ['/insights', 'Insights', <BarChart3 />],
      ['/security', 'Security', <Shield />, ov?.security_24h],
      ['/integrations', 'Integrations', <Plug />],
      ['/settings', 'Settings', <Settings />],
    ] },
  ]

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white"><Bot className="h-5 w-5" /></div>
          <div><div className="text-sm font-bold tracking-tight">Sahay</div><div className="text-[11px] text-slate-500">AI sales &amp; support teammate</div></div>
        </div>
        <div className="px-3">
          <select className="w-full rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm font-medium" value={current?.id || ''}
            onChange={(e) => { auth.setTenant(e.target.value); qc.clear(); nav('/inbox') }}>
            {tenants.map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
        <nav className="mt-3 flex-1 overflow-y-auto px-2 scroll-thin">
          {groups.map((g) => (
            <div key={g.label} className="mb-3">
              <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{g.label}</div>
              {g.items.map(([to, label, icon, badge]) => (
                <NavLink key={to} to={to} className={({ isActive }) => cx('flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm',
                  isActive || loc.pathname.startsWith(to) ? 'bg-brand-50 font-medium text-brand-700' : 'text-slate-600 hover:bg-slate-50')}>
                  <span className="[&>svg]:h-4 [&>svg]:w-4">{icon}</span><span className="flex-1">{label}</span>
                  {!!badge && <span className="rounded-full bg-slate-900 px-1.5 text-[10px] font-semibold text-white">{badge}</span>}
                </NavLink>
              ))}
            </div>
          ))}
          {me?.widget_key && (
            <a href={`/store/${me.widget_key}`} target="_blank" className="mx-1 mb-3 flex items-center gap-2 rounded-lg border border-dashed border-slate-300 px-2.5 py-2 text-xs text-slate-600 hover:bg-slate-50">
              <Store className="h-4 w-4" /> Open demo storefront ↗
            </a>
          )}
        </nav>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs">
          <div className="truncate"><div className="font-medium text-slate-700">{a.user?.name}</div><div className="truncate text-slate-400">{a.user?.email}</div></div>
          <button title="Sign out" onClick={() => { auth.clear(); location.href = '/login' }} className="text-slate-400 hover:text-slate-700"><LogOut className="h-4 w-4" /></button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto scroll-thin">
        <div className="mx-auto max-w-[1400px] px-6 py-6">{children}</div>
      </main>
    </div>
  )
}

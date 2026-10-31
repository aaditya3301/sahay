import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import DashboardLayout from './components/DashboardLayout'
import { auth } from './lib/api'
import Landing from './pages/Landing'

// Keep the public landing page independent of dashboard charts and editors.
const Approvals = lazy(() => import('./pages/Approvals'))
const Calls = lazy(() => import('./pages/Calls'))
const Campaigns = lazy(() => import('./pages/Campaigns'))
const Customers = lazy(() => import('./pages/Customers'))
const CustomerDetail = lazy(() => import('./pages/Customers').then((m) => ({ default: m.CustomerDetail })))
const InboxPage = lazy(() => import('./pages/Inbox'))
const Insights = lazy(() => import('./pages/Insights'))
const Integrations = lazy(() => import('./pages/Integrations'))
const Knowledge = lazy(() => import('./pages/Knowledge'))
const Leads = lazy(() => import('./pages/Leads'))
const LeadDetail = lazy(() => import('./pages/Leads').then((m) => ({ default: m.LeadDetail })))
const Learning = lazy(() => import('./pages/Learning'))
const Login = lazy(() => import('./pages/Auth'))
const Signup = lazy(() => import('./pages/Auth').then((m) => ({ default: m.Signup })))
const Policies = lazy(() => import('./pages/Policies'))
const Security = lazy(() => import('./pages/Security'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const Simulations = lazy(() => import('./pages/Simulations'))
const SimRun = lazy(() => import('./pages/Simulations').then((m) => ({ default: m.SimRun })))
const Simulator = lazy(() => import('./pages/Simulator'))
const StoreDemo = lazy(() => import('./pages/StoreDemo'))
const Studio = lazy(() => import('./pages/Studio'))
const TicketPage = lazy(() => import('./pages/Ticket'))

export default function App() {
  return (
    <Suspense fallback={<div className="min-h-full bg-[#eae6de] p-8 text-[#12374f]">Loading Sahay…</div>}>
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/store/:key" element={<StoreDemo />} />
      <Route path="/*" element={<Protected />} />
    </Routes>
    </Suspense>
  )
}

function Protected() {
  if (!auth.get()?.token) return <Navigate to="/login" replace />
  return (
    <DashboardLayout>
      <Routes>
        <Route path="/" element={<Navigate to="/inbox" replace />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/tickets/:id" element={<TicketPage />} />
        <Route path="/approvals" element={<Approvals />} />
        {/* Retained for internal use; the conversation tester has no frontend navigation entry. */}
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
        <Route path="/developer" element={<Integrations />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Routes>
    </DashboardLayout>
  )
}

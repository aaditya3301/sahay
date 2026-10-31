import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Card, PageHeader, Spinner } from '../components/ui'
import { api, auth } from '../lib/api'

const LANGUAGES: Record<string, string> = { en: 'English', hi: 'Hindi', hinglish: 'Hinglish' }
const PROVIDERS: Record<string, string> = { mock: 'Demo store', shopify: 'Shopify', woocommerce: 'WooCommerce', custom: 'Your own website' }

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="profile-row">
      <dt>{label}</dt>
      <dd>{children || <span className="profile-empty">Not added</span>}</dd>
    </div>
  )
}

export default function Profile() {
  const tenant = auth.get()?.tenant
  const { data: me } = useQuery({ queryKey: ['me', tenant], queryFn: () => api('/v1/me') })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: () => api('/v1/settings') })
  const { data: studio } = useQuery({ queryKey: ['studio'], queryFn: () => api('/v1/studio') })
  if (!me || !settings) return <Spinner />

  const s = settings.settings || {}
  const role = me.tenants?.find((t: any) => t.id === me.tenant_id)?.role
  const store = (settings.integrations || []).find((i: any) => i.kind === 'commerce')?.config || {}
  const provider = store.provider || 'mock'
  const initials = (me.user?.name || '').split(/\s+/).filter(Boolean).slice(0, 2).map((w: string) => w[0]).join('').toUpperCase()
  // The signup form stores extra lines (platform, customers…) after the first one: only the first is "what you sell"
  const sells = (s.business_description || '').split('\n')[0].trim()
  const website = s.website_url ? String(s.website_url).replace(/^https?:\/\//, '').replace(/\/$/, '') : ''

  return (
    <>
      <PageHeader title="Profile" subtitle="Who you are and the business you’re running on Sahay." />
      <div className="profile-grid">
        <Card title="Your profile">
          <div className="profile-person">
            <span className="profile-avatar" aria-hidden="true">{initials || 'S'}</span>
            <div><strong>{me.user?.name}</strong><span>{me.user?.email}</span></div>
          </div>
          <dl className="profile-list">
            <Row label="Name">{me.user?.name}</Row>
            <Row label="Email">{me.user?.email}</Row>
            <Row label="Role">{role ? role.charAt(0).toUpperCase() + role.slice(1) : ''}</Row>
          </dl>
        </Card>

        <Card title="Business profile" action={<Link to="/integrations?tab=business" className="setup-text-link">Edit →</Link>}>
          <dl className="profile-list">
            <Row label="Business name">{s.name}</Row>
            <Row label="What you sell">{sells}</Row>
            <Row label="Website">{website && <a className="setup-text-link" href={String(s.website_url)} target="_blank" rel="noreferrer">{website}</a>}</Row>
            <Row label="Store">{PROVIDERS[provider] || provider}{provider === 'shopify' && store.shop_name ? ` · ${store.shop_name}` : ''}</Row>
            <Row label="Languages">{(s.languages || []).map((l: string) => LANGUAGES[l] || l).join(', ')}</Row>
            <Row label="Your agent">{studio?.profile?.agent_name}</Row>
          </dl>
        </Card>
      </div>
    </>
  )
}

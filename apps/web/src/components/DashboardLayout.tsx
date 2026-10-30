import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight, BarChart3, BookOpen, Check, CheckSquare, ChevronDown, ChevronRight, FlaskConical, GraduationCap, Inbox, LogOut, Megaphone, Menu, Phone, Plug, Scale, Settings, Shield, Sparkles, Store, Target, Users, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api, auth } from '../lib/api'
import { cx } from './ui'
import './DashboardLayout.css'

type NavItem = { to: string; label: string; icon: ReactNode; count?: number }
type NavGroup = { id: string; label: string; icon: ReactNode; items: NavItem[] }
const belongsTo = (pathname: string, to: string) => pathname === to || pathname.startsWith(`${to}/`) || (to === '/inbox' && pathname.startsWith('/tickets/'))

function Wordmark() {
  return <span className="dashboard-wordmark"><img src="/sahay-mark-light.svg" alt="" width="30" height="30" /><span>sahay<span>.</span></span></span>
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase()

// A listbox instead of a native <select>, so the open list matches the dashboard (the OS one can't be styled).
function WorkspaceSwitcher({ tenants, currentId, onSelect }: { tenants: { id: string; name: string }[]; currentId?: string; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const listId = useId()
  const current = tenants.find((t) => t.id === currentId)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const show = () => { setActive(Math.max(0, tenants.findIndex((t) => t.id === currentId))); setOpen(true) }
  const choose = (i: number) => { setOpen(false); if (tenants[i]) onSelect(tenants[i].id) }
  const onKey = (e: React.KeyboardEvent) => {
    if (!open && ['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); show(); return }
    if (!open) return
    if (e.key === 'Escape' || e.key === 'Tab') setOpen(false)
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => (i + 1) % tenants.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => (i - 1 + tenants.length) % tenants.length) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(tenants.length - 1) }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(active) }
  }

  return (
    <div className="dashboard-workspace" ref={root}>
      <button type="button" className="dashboard-workspace-trigger" aria-label={`Workspace: ${current?.name || 'none'}. Switch workspace`}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} onClick={() => (open ? setOpen(false) : show())} onKeyDown={onKey}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}>
        <Store size={16} aria-hidden="true" />
        <span className="dashboard-workspace-name">{current?.name || 'Select workspace'}</span>
        <ChevronDown size={14} aria-hidden="true" className={cx('dashboard-workspace-chevron', open && 'is-open')} />
      </button>
      {open && (
        <ul id={listId} role="listbox" aria-label="Workspaces" className="dashboard-workspace-menu">
          <li className="dashboard-workspace-menu-label" role="presentation">Switch workspace</li>
          {tenants.map((t, i) => (
            <li key={t.id} id={`${listId}-${i}`} role="option" aria-selected={t.id === currentId}
              className={cx('dashboard-workspace-option', i === active && 'is-active', t.id === currentId && 'is-current')}
              onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(i)}>
              <span className="dashboard-workspace-avatar" aria-hidden="true">{initials(t.name)}</span>
              <span className="dashboard-workspace-option-name">{t.name}</span>
              {t.id === currentId && <Check size={15} aria-hidden="true" className="dashboard-workspace-check" />}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const a = auth.get()!
  const qc = useQueryClient()
  const nav = useNavigate()
  const { pathname } = useLocation()
  const dialog = useRef<HTMLDialogElement>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const main = useRef<HTMLElement>(null)
  const { data: ov } = useQuery({ queryKey: ['overview', a.tenant], queryFn: () => api('/v1/overview'), refetchInterval: 4000 })
  const { data: me } = useQuery({ queryKey: ['me', a.tenant], queryFn: () => api('/v1/me') })
  const tenants = me?.tenants || a.tenants || []
  const current = tenants.find((t: any) => t.id === (me?.tenant_id || a.tenant))
  const primary: NavItem[] = [
    { to: '/inbox', label: 'Inbox', icon: <Inbox />, count: ov?.open_tickets },
    { to: '/approvals', label: 'Approvals', icon: <CheckSquare />, count: ov?.pending_approvals },
    { to: '/customers', label: 'Customers', icon: <Users /> },
    { to: '/insights', label: 'Insights', icon: <BarChart3 /> },
  ]
  const groups: NavGroup[] = [
    { id: 'sales', label: 'Sales', icon: <Target />, items: [
      { to: '/leads', label: 'Leads', icon: <Target /> },
      { to: '/campaigns', label: 'Campaigns', icon: <Megaphone /> },
      { to: '/calls', label: 'Calls', icon: <Phone /> },
    ] },
    { id: 'agent', label: 'Agent', icon: <Sparkles />, items: [
      { to: '/studio', label: 'Agent Studio', icon: <Sparkles /> },
      { to: '/knowledge', label: 'Knowledge', icon: <BookOpen />, count: ov?.gap_suggestions },
      { to: '/policies', label: 'Policies', icon: <Scale /> },
      { to: '/learning', label: 'Learning', icon: <GraduationCap /> },
      { to: '/simulations', label: 'Simulations', icon: <FlaskConical /> },
    ] },
    { id: 'workspace', label: 'Workspace', icon: <Settings />, items: [
      { to: '/integrations', label: 'Integrations', icon: <Plug /> },
      { to: '/security', label: 'Security', icon: <Shield />, count: ov?.security_24h },
      { to: '/settings', label: 'Settings', icon: <Settings /> },
    ] },
  ]
  const activeGroup = groups.find((group) => group.items.some((item) => belongsTo(pathname, item.to)))
  const [expandedGroup, setExpandedGroup] = useState<string | null>(() => activeGroup?.id || null)
  const page = [...primary, ...groups.flatMap((group) => group.items)].find((item) => belongsTo(pathname, item.to))
  const pageName = pathname.startsWith('/tickets/') ? 'Conversation' : page?.label || 'Inbox'

  useEffect(() => {
    setExpandedGroup(activeGroup?.id || null)
    dialog.current?.close()
    main.current?.scrollTo({ top: 0 })
  }, [pathname, activeGroup?.id])

  useEffect(() => {
    const previous = document.title
    document.title = `${pageName} · Sahay`
    return () => { document.title = previous }
  }, [pageName])

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 769px)')
    const closeOnDesktop = () => { if (desktop.matches) dialog.current?.close() }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])

  const sidebar = (mobile = false) => <Sidebar
    primary={primary} groups={groups} pathname={pathname} expandedGroup={expandedGroup}
    onExpand={(id) => setExpandedGroup((previous) => previous === id ? null : id)}
    widgetKey={me?.widget_key} name={a.user?.name} email={a.user?.email}
    onNavigate={() => dialog.current?.close()}
    closeButton={mobile ? <button type="button" className="dashboard-icon-button" aria-label="Close navigation" onClick={() => dialog.current?.close()}><X size={20} /></button> : undefined}
  />

  return <div className="sahay-dashboard">
    <a href="#dashboard-content" className="dashboard-skip">Skip to content</a>
    <aside className="dashboard-sidebar" aria-label="Main navigation">{sidebar()}</aside>
    <header className="dashboard-topbar">
      <div className="dashboard-location">
        <button ref={menuButton} type="button" className="dashboard-menu-button dashboard-icon-button" aria-label="Open navigation" aria-haspopup="dialog" onClick={() => dialog.current?.showModal()}><Menu size={20} /></button>
        <span className="dashboard-location-parent">{activeGroup?.label || 'Customer care'}</span><ChevronRight className="dashboard-location-divider" size={14} aria-hidden="true" />
        <span className="dashboard-location-current">{pageName}</span>
      </div>
      <WorkspaceSwitcher tenants={tenants} currentId={current?.id}
        onSelect={(id) => { if (id !== current?.id) { auth.setTenant(id); qc.clear(); nav('/inbox') } }} />
    </header>
    <main id="dashboard-content" tabIndex={-1} ref={main} className="dashboard-sheet scroll-thin"><div className="dashboard-canvas">{children}</div></main>
    <dialog ref={dialog} className="dashboard-navigation-dialog" aria-label="Navigation" onClose={() => menuButton.current?.focus()} onClick={(event) => { if (event.target === event.currentTarget) dialog.current?.close() }}>
      <div className="sahay-dashboard dashboard-drawer">{sidebar(true)}</div>
    </dialog>
  </div>
}

function Sidebar({ primary, groups, pathname, expandedGroup, onExpand, widgetKey, name, email, onNavigate, closeButton }: {
  primary: NavItem[]; groups: NavGroup[]; pathname: string; expandedGroup: string | null; onExpand: (id: string) => void
  widgetKey?: string; name?: string; email?: string; onNavigate: () => void; closeButton?: ReactNode
}) {
  const id = useId()
  const navItem = (item: NavItem, nested = false) => <NavLink key={item.to} to={item.to} onClick={onNavigate}
    className={({ isActive }) => cx('dashboard-nav-item', nested && 'dashboard-nav-child', (isActive || belongsTo(pathname, item.to)) && 'is-active')}>
    <span className="dashboard-nav-icon" aria-hidden="true">{item.icon}</span><span>{item.label}</span>
    {!!item.count && <span className="dashboard-nav-count">{item.count}</span>}
  </NavLink>
  return <>
    <div className="dashboard-brand"><Wordmark />{closeButton}</div>
    <nav className="dashboard-nav scroll-thin" aria-label="Workspace navigation">
      <div className="dashboard-nav-primary">{primary.map((item) => navItem(item))}</div>
      <div className="dashboard-nav-groups">{groups.map((group) => {
        const expanded = expandedGroup === group.id
        const active = group.items.some((item) => belongsTo(pathname, item.to))
        const count = group.items.reduce((sum, item) => sum + (item.count || 0), 0)
        return <div key={group.id} className="dashboard-nav-group">
          <button type="button" className={cx('dashboard-nav-item dashboard-nav-toggle', active && 'has-active-child')} aria-expanded={expanded} aria-controls={`${id}-${group.id}`} onClick={() => onExpand(group.id)}>
            <span className="dashboard-nav-icon" aria-hidden="true">{group.icon}</span><span>{group.label}</span>
            {!expanded && count > 0 && <span className="dashboard-nav-count">{count}</span>}
            <ChevronDown size={15} className="dashboard-nav-chevron" aria-hidden="true" />
          </button>
          <div id={`${id}-${group.id}`} hidden={!expanded} className="dashboard-nav-children">{group.items.map((item) => navItem(item, true))}</div>
        </div>
      })}</div>
    </nav>
    <div className="dashboard-sidebar-bottom">
      {widgetKey && <a className="dashboard-store-link" href={`/store/${widgetKey}`} target="_blank" rel="noreferrer"><Store size={16} aria-hidden="true" /><span>Preview store</span><ArrowUpRight size={15} aria-hidden="true" /></a>}
      <div className="dashboard-account">
        <span className="dashboard-avatar" aria-hidden="true">{name?.split(' ').slice(0, 2).map((part) => part[0]).join('') || 'S'}</span>
        <div className="dashboard-account-name"><span>{name}</span><small title={email}>{email}</small></div>
        <button type="button" className="dashboard-icon-button" title="Sign out" aria-label="Sign out" onClick={() => { auth.clear(); location.href = '/login' }}><LogOut size={17} /></button>
      </div>
    </div>
  </>
}

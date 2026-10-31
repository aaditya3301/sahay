import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { ArrowRight, Check, Copy, LogOut, MessageCircle, Package, ShoppingBag, User, X } from 'lucide-react'
import { useParams } from 'react-router-dom'
import './StoreDemo.css'

type Product = { sku: string; title: string; description?: string; price_inr: number; in_stock: boolean; image?: string }
type StoreConfig = { name: string; slug: string; brand_color?: string; agent_name?: string }
type Placed = { order_id: string; paid: boolean; demo: boolean; total_inr: number; items: { title: string; qty: number }[]; expected_delivery?: string | null; customer_name: string }
type OrderView = Placed & { status: string; placed_at?: string | null; delivered_at?: string | null; shipment_status?: string | null; phone: string }
const STATUS: Record<string, string> = { payment_pending: 'Payment pending', processing: 'Confirmed, packing', in_transit: 'On the way', delivered: 'Delivered', cancelled: 'Cancelled' }
const shortDate = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : ''

// Cashfree's hosted checkout (JS SDK v3): load once, then redirect the shopper to the payment page
const CASHFREE_SDK = 'https://sdk.cashfree.com/js/v3/cashfree.js'
function loadCashfree(): Promise<any> {
  const w = window as any
  if (w.Cashfree) return Promise.resolve(w.Cashfree)
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = CASHFREE_SDK; s.async = true
    s.onload = () => resolve(w.Cashfree); s.onerror = () => reject(new Error('The payment page couldn’t load. Check your connection.'))
    document.head.appendChild(s)
  })
}
// A shopper's account on this store. The session is kept in this browser for a month, so they log in once, not every visit.
type Shopper = { name: string; email: string; phone: string }
type Session = { token: string; shopper: Shopper; chat?: { user: Shopper; userHash: string } }  // chat: the shopper, signed, for the chat widget
const sessionKey = (key: string) => `sahay.shopper.${key}`
const loadSession = (key: string): Session | null => { try { return JSON.parse(localStorage.getItem(sessionKey(key)) || 'null') } catch { return null } }
const saveSession = (key: string, s: Session) => { try { localStorage.setItem(sessionKey(key), JSON.stringify(s)) } catch { /* private mode: they just log in again next time */ } }
const clearSession = (key: string) => { try { localStorage.removeItem(sessionKey(key)) } catch { /* nothing to clear */ } }
const shopperCall = async (path: string, body: any) => {
  const r = await fetch(`/v1/widget/shopper/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || 'Something went wrong. Please try again.')
  return d
}
const money = (amount: number) => `₹${amount.toLocaleString('en-IN')}`

// Demo commerce stays on the existing widget API: product context, cart reporting and checkout.
export default function StoreDemo() {
  const { key } = useParams()
  const [cfg, setCfg] = useState<StoreConfig | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [viewing, setViewing] = useState<string | null>(null)
  const [featuredSku, setFeaturedSku] = useState<string | null>(null)
  const [cart, setCart] = useState<Record<string, number>>({})
  const [checkout, setCheckout] = useState(false)
  const [contact, setContact] = useState({ name: '', phone: '', email: '' })
  const [session, setSession] = useState<Session | null>(() => (key ? loadSession(key) : null))
  const shopper = session?.shopper || null
  const [authOpen, setAuthOpen] = useState(false)
  const [authMode, setAuthMode] = useState<'signup' | 'login'>('signup')
  const [authForm, setAuthForm] = useState({ name: '', email: '', password: '', phone: '' })
  const [authError, setAuthError] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const afterLogin = useRef<((who?: { name: string; phone: string; email: string }) => void) | null>(null)
  const authDialog = useRef<HTMLDialogElement>(null)
  const [paying, setPaying] = useState(false)
  const [payError, setPayError] = useState('')
  const [placed, setPlaced] = useState<Placed | null>(null)
  const [copied, setCopied] = useState(false)
  const [ordersOpen, setOrdersOpen] = useState(false)
  const [myOrders, setMyOrders] = useState<OrderView[]>([])
  const [loadingOrders, setLoadingOrders] = useState(false)
  const ordersDialog = useRef<HTMLDialogElement>(null)
  const [loadError, setLoadError] = useState('')
  const storefront = useRef<HTMLDivElement>(null)
  const checkoutDialog = useRef<HTMLDialogElement>(null)
  const placedDialog = useRef<HTMLDialogElement>(null)

  // Back from Cashfree: /store/<key>?order_id=…  → confirm the payment and show the order id
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const orderId = params.get('order_id')
    if (!orderId || !key) return
    window.history.replaceState(null, '', window.location.pathname)
    fetch('/v1/widget/checkout/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, order_id: orderId }) })
      .then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.detail || 'We couldn’t find that order.'); return d })
      .then((d: OrderView) => {
        setPlaced(d); setCart({})
        // tell the chat assistant about the order, once the widget has loaded
        let tries = 0
        const tell = () => { const sahay = (window as any).Sahay; if (sahay) sahay('setContext', { order_id: d.order_id, order_paid: d.paid }); else if (tries++ < 20) setTimeout(tell, 500) }
        tell()
      })
      .catch((e) => setPayError(e.message))
  }, [key])

  useEffect(() => {
    if (!key || !session) return
    fetch(`/v1/widget/shopper/me?key=${encodeURIComponent(key)}`, { headers: { Authorization: `Bearer ${session.token}` } })
      .then(async (r) => {
        if (r.status === 401) { clearSession(key); setSession(null); return }
        if (r.ok) { const d = await r.json(); const merged = { ...session, shopper: d.shopper, chat: d.chat }; saveSession(key, merged); setSession(merged) }
      })
      .catch(() => { /* offline: keep the remembered session */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    if (shopper) setContact({ name: shopper.name, phone: shopper.phone, email: shopper.email })
  }, [shopper])

  useEffect(() => {
    const dialog = authDialog.current
    if (authOpen && dialog && !dialog.open) dialog.showModal()
    else if (!authOpen && dialog?.open) dialog.close()
  }, [authOpen])

  useEffect(() => {
    const dialog = placedDialog.current
    if (placed && dialog && !dialog.open) dialog.showModal()
  }, [placed])

  useEffect(() => {
    const dialog = ordersDialog.current
    if (ordersOpen && dialog && !dialog.open) dialog.showModal()
    else if (!ordersOpen && dialog?.open) dialog.close()
  }, [ordersOpen])

  // "My orders" lists what this account has bought: no order id or phone number to type
  const openOrders = async () => {
    setOrdersOpen(true)
    if (!key || !session) return
    setLoadingOrders(true)
    try {
      const r = await fetch(`/v1/widget/shopper/orders?key=${encodeURIComponent(key)}`, { headers: { Authorization: `Bearer ${session.token}` } })
      if (r.status === 401) { clearSession(key); setSession(null); setOrdersOpen(false); return }
      setMyOrders(r.ok ? await r.json() : [])
    } catch { setMyOrders([]) } finally { setLoadingOrders(false) }
  }

  useEffect(() => {
    const controller = new AbortController()
    setCfg(null); setProducts([]); setLoadError(''); setFeaturedSku(null)
    const get = async (path: string) => {
      const response = await fetch(`${path}?key=${encodeURIComponent(key || '')}`, { signal: controller.signal })
      if (!response.ok) throw new Error('We couldn’t load this store. Please try again.')
      return response.json()
    }
    Promise.all([get('/v1/widget/config'), get('/v1/widget/products')])
      .then(([config, catalogue]) => { setCfg(config); setProducts(catalogue) })
      .catch((error) => { if (error.name !== 'AbortError') setLoadError(error.message) })
    return () => controller.abort()
  }, [key])

  // The chat is for logged-in shoppers: its launcher only exists while someone is logged in, and goes away on log out.
  const chat = session?.chat
  useEffect(() => {
    if (!key || !chat) return
    const s = document.createElement('script')
    // started by hand so it can be given the logged-in shopper (signed by the server): the agent then knows them and their orders
    s.src = '/widget.js'; s.dataset.manual = '1'; s.async = true
    s.onload = () => (window as any).Sahay?.('init', { siteKey: key, user: chat.user, userHash: chat.userHash })
    document.body.appendChild(s)
    return () => {
      s.remove()
      document.getElementById('sahay-launcher')?.remove()
      document.getElementById('sahay-frame-wrap')?.remove()
      document.querySelectorAll('[id^="sahay-"]').forEach((e) => e.remove())
      delete (window as any).Sahay
    }
  }, [key, chat?.userHash])

  useEffect(() => {
    if (!cfg) return
    const previous = document.title
    document.title = `${cfg.name} | Shop`
    return () => { document.title = previous }
  }, [cfg])

  useEffect(() => {
    if (!products.length || !storefront.current || !('IntersectionObserver' in window)) return
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target) }
      })
    }, { threshold: 0.08 })
    storefront.current.querySelectorAll('[data-store-reveal]').forEach((element) => observer.observe(element))
    return () => observer.disconnect()
  }, [products])

  useEffect(() => {
    const dialog = checkoutDialog.current
    if (checkout && dialog && !dialog.open) dialog.showModal()
    else if (!checkout && dialog?.open) dialog.close()
  }, [checkout])

  const S = () => (window as any).Sahay
  const openHelp = () => S()?.('open')
  const ask = (p: Product) => {
    setViewing(p.sku)
    S()?.('setContext', { productSku: p.sku, productTitle: p.title })
    openHelp()
  }
  const items = Object.entries(cart).filter(([, q]) => q > 0).map(([sku, qty]) => ({ sku, qty }))
  const report = (next: Record<string, number>, who = contact) => {
    const list = Object.entries(next).filter(([, q]) => q > 0).map(([sku, qty]) => ({ sku, qty }))
    S()?.('cart', { items: list, name: who.name || undefined, phone: who.phone || undefined })
    S()?.('setContext', { cart: list })
  }
  const addToCart = (p: Product, who?: { name: string; phone: string; email: string }) => { const next = { ...cart, [p.sku]: (cart[p.sku] || 0) + 1 }; setCart(next); report(next, who || contact) }
  // Browsing is open to everyone; adding to the cart and checking out need an account.
  const needLogin = (then?: (who?: { name: string; phone: string; email: string }) => void) => {
    if (session) { then?.(); return }
    afterLogin.current = then || null; setAuthError(''); setAuthOpen(true)
  }
  const add = (p: Product) => needLogin((who) => addToCart(p, who))
  const openCart = () => needLogin(() => setCheckout(true))
  const submitAuth = async () => {
    setAuthError(''); setAuthBusy(true)
    try {
      const d = authMode === 'signup'
        ? await shopperCall('signup', { key, ...authForm })
        : await shopperCall('login', { key, email: authForm.email, password: authForm.password })
      const next: Session = { token: d.token, shopper: d.shopper, chat: d.chat }
      saveSession(key!, next); setSession(next); setAuthOpen(false); setAuthForm({ name: '', email: '', password: '', phone: '' })
      const todo = afterLogin.current; afterLogin.current = null
      todo?.({ name: d.shopper.name, phone: d.shopper.phone, email: d.shopper.email })
    } catch (e: any) { setAuthError(e.message || String(e)) } finally { setAuthBusy(false) }
  }
  const logOut = () => { if (key) clearSession(key); setSession(null); setCart({}); setContact({ name: '', phone: '', email: '' }); S()?.('cart', { items: [] }) }
  const pay = async () => {
    setPayError(''); setPaying(true)
    report(cart)
    try {
      const r = await fetch('/v1/widget/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, items, shopper: session?.token }) })
      const d = await r.json()
      if (r.status === 401) { if (key) clearSession(key); setSession(null); setCheckout(false); setPaying(false); needLogin(() => setCheckout(true)); return }
      if (!r.ok) throw new Error(d.detail || 'The payment page couldn’t be opened.')
      const Cashfree = await loadCashfree()
      await Cashfree({ mode: d.mode }).checkout({ paymentSessionId: d.payment_session_id, redirectTarget: '_self' })
    } catch (e: any) { setPayError(e.message || String(e)); setPaying(false) }
  }
  const copyOrderId = async () => {
    if (!placed) return
    try { await navigator.clipboard.writeText(placed.order_id); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch { /* the id is on screen */ }
  }
  const total = items.reduce((t, i) => t + (products.find((p) => p.sku === i.sku)?.price_inr || 0) * i.qty, 0)
  const count = items.reduce((n, i) => n + i.qty, 0)
  const farm = cfg?.slug === 'ganga-farms'
  const loop = cfg?.slug === 'loop-loot'
  const featuredProducts = (farm ? ['GF-GHE-1L', 'GF-HNY-500', 'GF-OIL-1L'] : ['CHM-GFT-10', 'CHM-SET-07', 'CHM-ANK-04'])
    .map((sku) => products.find((p) => p.sku === sku)).filter((p): p is Product => !!p)
  const highlights = featuredProducts.length ? featuredProducts : products.slice(0, 3)
  const featured = highlights.find((p) => p.sku === featuredSku) || highlights[0]
  const storyProduct = products.find((p) => p.sku === (farm ? 'GF-HNY-500' : 'CHM-GFT-10')) || products[1] || featured
  const shortName = (p: Product) => ({ 'GF-GHE-1L': 'A2 ghee', 'GF-HNY-500': 'Forest honey', 'GF-OIL-1L': 'Mustard oil', 'CHM-GFT-10': 'Gift box', 'CHM-SET-07': 'Celestial charms', 'CHM-ANK-04': 'Boho anklet' }[p.sku] || p.title)

  if (!cfg) return <div className="storefront store-loading" role={loadError ? 'alert' : 'status'}><p>{loadError || 'Opening the shop…'}</p>{loadError && <button className="store-button" onClick={() => window.location.reload()}>Try again</button>}</div>

  return (
    <div ref={storefront} className={`storefront ${farm ? 'storefront--farm' : loop ? 'storefront--loop' : ''}`} style={{ '--store-accent': cfg.brand_color || '#226192' } as CSSProperties}>
      <header className="store-header">
        <a className="store-wordmark" href="#store-top">{cfg.name}<span aria-hidden="true">.</span></a>
        <nav aria-label="Store navigation">
          <a href="#store-shop">Shop</a><a href="#store-story">{farm ? 'Our roots' : 'Our story'}</a>
        </nav>
        {shopper ? (
          <details className="store-account">
            <summary aria-label={`Account: ${shopper.name}`}><span className="store-avatar" aria-hidden="true">{shopper.name.charAt(0).toUpperCase()}</span><span className="store-account-name">{shopper.name.split(' ')[0]}</span></summary>
            <div className="store-account-menu">
              <strong>{shopper.name}</strong><span>{shopper.email}</span><span>{shopper.phone}</span>
              <button type="button" onClick={(e) => { (e.currentTarget.closest('details') as HTMLDetailsElement).open = false; openOrders() }}><Package size={15} aria-hidden="true" />My orders</button>
              <button type="button" onClick={logOut}><LogOut size={15} aria-hidden="true" />Log out</button>
            </div>
          </details>
        ) : (
          <button className="store-login" type="button" onClick={() => needLogin()}><User size={16} aria-hidden="true" />Log in</button>
        )}
        {shopper && <button className="store-cart" onClick={openCart} aria-label={`Open cart, ${count} ${count === 1 ? 'item' : 'items'}`}><ShoppingBag size={18} /><span>Cart</span><span className="store-cart-count">{count}</span></button>}
      </header>

      <main id="store-top">
        <section className={`store-hero ${featured ? '' : 'store-hero--empty'}`} aria-labelledby="store-title">
          <div className="store-hero-copy">
            <h1 id="store-title">{farm ? <>From our farm.<br />For your home.</> : loop ? <>Little things.<br />Big personality.</> : cfg.name}</h1>
            <p>{farm ? 'A2 ghee, forest honey and pantry favourites from Uttarakhand, delivered to your door.' : loop ? 'Charms, bracelets and little gifts that feel a lot like you.' : 'Explore our collection and find your next favourite.'}</p>
            <a className="store-button" href="#store-shop">{farm ? 'Shop the pantry' : 'Shop the collection'}<ArrowRight size={18} /></a>
            <div className="store-hero-note">{farm ? 'Rooted in Uttarakhand. Shared across India.' : loop ? 'Wear it. Gift it. Make it yours.' : 'A little something for every day.'}</div>
          </div>
          {featured && <div className="store-specimen">
            <div className="store-specimen-picture" key={featured.sku}>
              {featured.image && <img src={featured.image} alt={featured.title} width="900" height="900" fetchPriority="high" />}
            </div>
            <div className="store-specimen-caption"><span>{featured.title}</span><span>{money(featured.price_inr)}</span></div>
            <div className="store-specimen-choices" aria-label="Featured products">{highlights.map((p) => <button key={p.sku} aria-pressed={featured.sku === p.sku} onClick={() => setFeaturedSku(p.sku)}>{shortName(p)}</button>)}</div>
          </div>}
        </section>

        <section id="store-shop" className="store-catalogue" aria-labelledby="store-shop-title">
          <div className="store-section-heading" data-store-reveal><div><h2 id="store-shop-title">{farm ? 'Your everyday pantry' : 'Find your little favourite'}</h2><p>{farm ? 'Good ingredients. Familiar favourites.' : 'For your wrist, your bag or your favourite person.'}</p></div><span>{products.length} products</span></div>
          <div className="store-products">
            {products.map((p, i) => (
              <article key={p.sku} className={`store-product ${viewing === p.sku ? 'store-product--selected' : ''}`} data-store-reveal style={{ '--store-delay': `${(i % 4) * 70}ms` } as CSSProperties}>
                <button className="store-product-image" onClick={() => ask(p)} aria-label={`Ask about ${p.title}`}>
                  {p.image ? <img src={p.image} alt={p.title} width="600" height="600" loading="lazy" /> : <span>{p.title}</span>}
                </button>
                <div className="store-product-info"><h3>{p.title}</h3><div className="store-product-meta"><span className="store-price">{money(p.price_inr)}</span><span className={p.in_stock === false ? 'store-stock store-stock--out' : 'store-stock'}>{p.in_stock === false ? 'Out of stock' : 'In stock'}</span></div>
                  <div className="store-product-actions"><button className="store-add" onClick={() => add(p)}>Add to cart{cart[p.sku] ? ` (${cart[p.sku]})` : ''}</button><button className="store-ask" onClick={() => ask(p)}>Ask about this<ArrowRight size={15} /></button></div>
                </div>
              </article>
            ))}
          </div>
          {!products.length && <div className="store-catalogue-empty"><p>The collection is being prepared.</p><button className="store-button" onClick={openHelp}>Ask about products</button></div>}
        </section>

        <section id="store-story" className={`store-story ${storyProduct?.image ? '' : 'store-story--text'}`} aria-labelledby="store-story-title">
          {storyProduct?.image && <div className="store-story-picture" data-store-reveal><img src={storyProduct.image} alt={storyProduct.title} width="700" height="700" loading="lazy" /></div>}
          <div className="store-story-copy" data-store-reveal><h2 id="store-story-title">{farm ? 'A little closer to the farm' : loop ? 'Good things come in loops' : 'Made for your everyday'}</h2>
            <p>{farm ? 'From Bilona-method A2 ghee to raw forest honey and cold-pressed oils, our pantry brings a taste of Uttarakhand to your kitchen.' : loop ? 'A moon on your bracelet. A tiny charm on your keys. A matching gift for your bestie. Little details, with a story of their own.' : 'Find something you love, and ask us if you need a hand choosing.'}</p>
            <p>{farm ? 'Shopping for your family, your café or your store? Ask Ganga about ingredients, your order or buying in bulk.' : loop ? 'Choosing a gift or planning something bigger? Ask Lia about the collection, your order or bulk gifting.' : 'Our assistant can help with products, orders and gifting.'}</p>
            <button className="store-button" onClick={openHelp}><MessageCircle size={18} />{`Ask ${cfg.agent_name || 'us'}`}</button>
          </div>
        </section>
      </main>

      <footer className="store-footer"><div className="store-footer-top"><p>{farm ? 'From Uttarakhand, with care.' : loop ? 'A little joy. On repeat.' : 'Find your next favourite.'}</p><a href="#store-shop">Back to the shop<ArrowRight size={17} /></a></div><div className="store-footer-name" aria-hidden="true">{cfg.name}<span>.</span></div><div className="store-footer-bottom"><span>© {new Date().getFullYear()} {cfg.name}</span><span>Demo storefront powered by Sahay</span><button onClick={openHelp}>Order help</button></div></footer>

      <dialog ref={checkoutDialog} className="store-checkout" onCancel={() => setCheckout(false)} onClose={() => setCheckout(false)} onClick={(e) => { if (e.target === e.currentTarget) setCheckout(false) }} aria-labelledby="checkout-title">
        <div className="store-checkout-inner" data-ui>
          <div className="store-checkout-heading"><h2 id="checkout-title">Your cart</h2><button onClick={() => setCheckout(false)} aria-label="Close cart"><X size={22} /></button></div>
          {!items.length ? <div className="store-cart-empty"><ShoppingBag size={32} /><p>Your cart is waiting for a favourite.</p><button className="store-button" onClick={() => setCheckout(false)}>Keep shopping</button></div> : <>
            <div className="store-checkout-items">{items.map((i) => <div key={i.sku}><span>{i.qty} × {products.find((p) => p.sku === i.sku)?.title}</span><span>{money((products.find((p) => p.sku === i.sku)?.price_inr || 0) * i.qty)}</span></div>)}</div>
            <div className="store-checkout-total"><span>Total</span><span>{money(total)}</span></div>
            <div className="store-ordering-as"><span>Ordering as</span><strong>{shopper?.name}</strong><span>{shopper?.phone} · {shopper?.email}</span></div>
            {payError && <p className="store-pay-error" role="alert">{payError}</p>}
            <button className="store-button store-checkout-continue" onClick={pay} disabled={paying || !shopper}>
              {paying ? 'Opening secure payment…' : <>Pay {money(total)} securely<ArrowRight size={18} /></>}
            </button>
            <p className="store-demo-note">Payments by Cashfree (sandbox). No real money is taken, and returning from the payment page confirms the order.</p>
          </>}
        </div>
      </dialog>
      <dialog ref={authDialog} className="store-checkout store-auth" onCancel={() => setAuthOpen(false)} onClose={() => setAuthOpen(false)} onClick={(e) => { if (e.target === e.currentTarget) setAuthOpen(false) }} aria-labelledby="auth-title">
        <div className="store-checkout-inner" data-ui>
          <div className="store-checkout-heading"><h2 id="auth-title">{authMode === 'signup' ? 'Create your account' : 'Welcome back'}</h2><button onClick={() => setAuthOpen(false)} aria-label="Close"><X size={22} /></button></div>
          <p className="store-demo-note">{authMode === 'signup' ? 'Sign up to add things to your cart and check out. We’ll remember you on this device.' : 'Log in to add things to your cart and check out.'}</p>
          <div className="store-auth-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={authMode === 'signup'} className={authMode === 'signup' ? 'is-on' : ''} onClick={() => { setAuthMode('signup'); setAuthError('') }}>Create account</button>
            <button type="button" role="tab" aria-selected={authMode === 'login'} className={authMode === 'login' ? 'is-on' : ''} onClick={() => { setAuthMode('login'); setAuthError('') }}>Log in</button>
          </div>
          <form onSubmit={(e) => { e.preventDefault(); submitAuth() }} noValidate>
            {authMode === 'signup' && <label>Your name<input autoComplete="name" placeholder="Name" value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} required /></label>}
            <label>Email<input type="email" autoComplete="email" placeholder="you@example.com" value={authForm.email} onChange={(e) => setAuthForm({ ...authForm, email: e.target.value })} required /></label>
            <label>Password<input type="password" autoComplete={authMode === 'signup' ? 'new-password' : 'current-password'} placeholder={authMode === 'signup' ? 'At least 8 characters' : 'Your password'} value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} required /></label>
            {authMode === 'signup' && <label>Mobile number<input type="tel" autoComplete="tel" inputMode="numeric" placeholder="10-digit mobile number" value={authForm.phone} onChange={(e) => setAuthForm({ ...authForm, phone: e.target.value })} required /></label>}
            {authError && <p className="store-pay-error" role="alert">{authError}</p>}
            <button className="store-button store-checkout-continue" type="submit" disabled={authBusy}>{authBusy ? 'Please wait…' : authMode === 'signup' ? 'Create account' : 'Log in'}<ArrowRight size={18} /></button>
          </form>
        </div>
      </dialog>
      <dialog ref={placedDialog} className="store-checkout store-placed" onClose={() => setPlaced(null)} aria-labelledby="placed-title">
        {placed && <div className="store-checkout-inner" data-ui>
          <div className="store-checkout-heading"><h2 id="placed-title">{placed.paid ? 'Order placed' : 'Payment pending'}</h2><button onClick={() => placedDialog.current?.close()} aria-label="Close"><X size={22} /></button></div>
          <p>{placed.paid ? `Thank you, ${placed.customer_name.split(' ')[0]}! Your order is confirmed.` : 'We haven’t received the payment yet. If you paid, it will confirm shortly.'}</p>
          <div className="store-order-id"><span>Your order ID</span><strong>{placed.order_id}</strong>
            <button onClick={copyOrderId} aria-label="Copy order ID">{copied ? <><Check size={16} />Copied</> : <><Copy size={16} />Copy</>}</button></div>
          <div className="store-checkout-items">{placed.items.map((i) => <div key={i.title}><span>{i.qty} × {i.title}</span></div>)}</div>
          <div className="store-checkout-total"><span>Paid</span><span>{money(placed.total_inr)}</span></div>
          {placed.expected_delivery && <p className="store-demo-note">Expected delivery by {new Date(placed.expected_delivery).toLocaleDateString('en-IN', { day: 'numeric', month: 'long' })}.</p>}
          <p className="store-demo-note">Keep this ID. Quote it when you chat with {cfg.agent_name || 'us'} or call us, and we’ll pull up your order straight away.</p>
          {placed.demo && <p className="store-demo-note">Sandbox demo: marked as paid when you returned from the payment page.</p>}
          <button className="store-button" onClick={() => { placedDialog.current?.close(); openHelp() }}><MessageCircle size={18} />Ask about this order</button>
        </div>}
      </dialog>
      <dialog ref={ordersDialog} className="store-checkout store-orders" onCancel={() => setOrdersOpen(false)} onClose={() => setOrdersOpen(false)} onClick={(e) => { if (e.target === e.currentTarget) setOrdersOpen(false) }} aria-labelledby="orders-title">
        <div className="store-checkout-inner" data-ui>
          <div className="store-checkout-heading"><h2 id="orders-title">My orders</h2><button onClick={() => setOrdersOpen(false)} aria-label="Close"><X size={22} /></button></div>
          {loadingOrders && <p className="store-demo-note">Loading your orders…</p>}
          {!loadingOrders && !myOrders.length && <div className="store-cart-empty"><Package size={32} /><p>You haven’t placed any orders yet.</p></div>}
          <div className="store-order-list">
            {myOrders.map((o) => (
              <article key={o.order_id} className="store-order-card">
                <div className="store-order-card-head"><strong>{o.order_id}</strong><span className={`store-order-status store-order-status--${o.status}`}>{STATUS[o.status] || o.status}</span></div>
                <p>{o.items.map((i) => `${i.qty} × ${i.title}`).join(', ')}</p>
                <div className="store-order-card-meta">
                  <span>{money(o.total_inr)}</span>
                  {o.placed_at && <span>Ordered {shortDate(o.placed_at)}</span>}
                  {o.delivered_at ? <span>Delivered {shortDate(o.delivered_at)}</span> : o.expected_delivery && <span>Arrives by {shortDate(o.expected_delivery)}</span>}
                </div>
                <button className="store-ask" onClick={async () => { try { await navigator.clipboard.writeText(o.order_id) } catch { /* shown above */ } }}><Copy size={14} />Copy order ID</button>
              </article>
            ))}
          </div>
          <p className="store-demo-note">These are the orders on your account. Chatting with {cfg.agent_name || 'us'} about one? They already know it is yours.</p>
        </div>
      </dialog>
      {payError && !checkout && <div className="store-toast" role="alert"><span>{payError}</span><button aria-label="Dismiss notification" onClick={() => setPayError('')}><X size={18} /></button></div>}
    </div>
  )
}

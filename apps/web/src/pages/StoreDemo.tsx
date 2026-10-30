import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

// A fake storefront for the tenant, with the real embeddable widget loaded via <script>.
// Clicking a product passes it to the agent as page context ("is this in stock?" just works).
// The cart is reported with Sahay('cart', ...): leave checkout without ordering and the lifecycle worker sends one reminder.
export default function StoreDemo() {
  const { key } = useParams()
  const [cfg, setCfg] = useState<any>(null)
  const [products, setProducts] = useState<any[]>([])
  const [viewing, setViewing] = useState<string | null>(null)
  const [cart, setCart] = useState<Record<string, number>>({})
  const [checkout, setCheckout] = useState(false)
  const [contact, setContact] = useState({ name: '', phone: '' })
  const [left, setLeft] = useState(false)
  useEffect(() => {
    fetch(`/v1/widget/config?key=${key}`).then((r) => r.json()).then(setCfg)
    fetch(`/v1/widget/products?key=${key}`).then((r) => r.json()).then(setProducts)
    const s = document.createElement('script')
    s.src = '/widget.js'; s.dataset.siteKey = key!; s.async = true
    document.body.appendChild(s)
    return () => {
      s.remove()
      document.getElementById('sahay-launcher')?.remove()
      document.getElementById('sahay-frame-wrap')?.remove()
      document.querySelectorAll('[id^="sahay-"]').forEach((e) => e.remove())
      delete (window as any).Sahay
    }
  }, [key])
  const S = () => (window as any).Sahay
  const ask = (p: any) => {
    setViewing(p.sku)
    S()?.('setContext', { productSku: p.sku, productTitle: p.title })
    S()?.('open')
  }
  const items = Object.entries(cart).filter(([, q]) => q > 0).map(([sku, qty]) => ({ sku, qty }))
  const report = (next: Record<string, number>, who = contact) => {
    const list = Object.entries(next).filter(([, q]) => q > 0).map(([sku, qty]) => ({ sku, qty }))
    S()?.('cart', { items: list, name: who.name || undefined, phone: who.phone || undefined })
    S()?.('setContext', { cart: list })
  }
  const add = (p: any) => { const next = { ...cart, [p.sku]: (cart[p.sku] || 0) + 1 }; setCart(next); report(next) }
  const total = items.reduce((t, i) => t + (products.find((p) => p.sku === i.sku)?.price_inr || 0) * i.qty, 0)
  const color = cfg?.brand_color || '#4f46e5'
  return (
    <div className="min-h-full bg-white">
      <header className="flex items-center justify-between px-8 py-5" style={{ borderBottom: `3px solid ${color}` }}>
        <div className="text-2xl font-black tracking-tight" style={{ color }}>{cfg?.name || '…'}</div>
        <nav className="flex items-center gap-6 text-sm text-slate-600"><span>Shop</span><span>New in</span><span>Bulk & gifting</span><span>Track order</span>
          <button onClick={() => setCheckout(true)} className="rounded-full px-3 py-1 font-semibold text-white" style={{ background: color }}>Cart · {items.reduce((n, i) => n + i.qty, 0)}</button></nav>
      </header>
      <section className="px-8 py-16 text-center" style={{ background: `linear-gradient(135deg, ${color}18, #fff)` }}>
        <h1 className="text-4xl font-bold text-slate-900">Welcome to {cfg?.name}</h1>
        <p className="mt-3 text-slate-600">Questions about a product, bulk orders or your order? Tap the chat bubble, or "Ask about this" on any product. Our AI replies in seconds, in Hindi, Hinglish or English.</p>
      </section>
      <section className="grid grid-cols-2 gap-6 px-8 py-10 md:grid-cols-4">
        {products.map((p) => (
          <div key={p.sku} className="overflow-hidden rounded-xl border shadow-sm transition hover:shadow-md"
            style={{ borderColor: viewing === p.sku ? color : '#f1f5f9' }}>
            {p.image ? <img src={p.image} onClick={() => ask(p)} className="aspect-square w-full cursor-pointer object-cover" /> : <div className="aspect-square bg-slate-100" />}
            <div className="p-3">
              <div className="text-sm font-medium text-slate-800">{p.title}</div>
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold" style={{ color }}>₹{p.price_inr.toLocaleString('en-IN')}</div>
                <div className={`text-[11px] ${p.in_stock === false ? 'text-red-600' : 'text-emerald-700'}`}>{p.in_stock === false ? 'Out of stock' : 'In stock'}</div>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-1.5">
                <button onClick={() => add(p)} className="rounded-lg border py-1.5 text-xs font-semibold" style={{ borderColor: color, color }}>Add to cart{cart[p.sku] ? ` (${cart[p.sku]})` : ''}</button>
                <button onClick={() => ask(p)} className="rounded-lg py-1.5 text-xs font-semibold text-white" style={{ background: color }}>Ask about this</button>
              </div>
            </div>
          </div>
        ))}
      </section>
      {checkout && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={() => setCheckout(false)}>
          <div className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-lg font-bold">Checkout</div>
            {!items.length ? <p className="mt-2 text-sm text-slate-500">Your cart is empty.</p> : (
              <>
                <div className="mt-3 space-y-1 text-sm">
                  {items.map((i) => <div key={i.sku} className="flex justify-between"><span>{i.qty} × {products.find((p) => p.sku === i.sku)?.title}</span></div>)}
                  <div className="flex justify-between border-t pt-1 font-semibold"><span>Total</span><span>₹{total.toLocaleString('en-IN')}</span></div>
                </div>
                <input className="mt-3 w-full rounded-lg border px-3 py-2 text-sm" placeholder="Name" value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} />
                <input className="mt-2 w-full rounded-lg border px-3 py-2 text-sm" placeholder="WhatsApp number" value={contact.phone}
                  onChange={(e) => setContact({ ...contact, phone: e.target.value })} onBlur={() => report(cart)} />
                <button onClick={() => { report(cart); setLeft(true); setCheckout(false) }} className="mt-3 w-full rounded-lg py-2 text-sm font-semibold text-white" style={{ background: color }}>
                  Continue to payment
                </button>
                <p className="mt-2 text-[11px] text-slate-400">Demo store: payment isn't real. Leaving here counts as an abandoned cart; the AI sends one WhatsApp reminder later (see Campaigns → Proactive & automations).</p>
              </>
            )}
          </div>
        </div>
      )}
      {left && <div className="fixed bottom-6 left-6 z-30 max-w-xs rounded-xl bg-slate-900 px-4 py-3 text-xs text-white shadow-lg">Cart saved as abandoned (demo). A reminder goes out on the next automation run.</div>}
    </div>
  )
}

// @sahay/node: server-side SDK for businesses with their own website.
//   const sahay = new Sahay(process.env.SAHAY_SECRET_KEY)
//   sahay.identify({ email })                      → { user, userHash } for the widget (level 2)
//   await sahay.events.send('order.shipped', {...}) → keep the AI up to date (level 2)
//   app.post('/sahay', sahay.handler({ getOrder, findOrders, checkStock, createReturn }))   (level 3)
import crypto from 'node:crypto'

const DEFAULT_HOST = 'https://sahay-536440059442.asia-south1.run.app'
const TOLERANCE_S = 300

export class Sahay {
  constructor(secretKey, { host = DEFAULT_HOST } = {}) {
    if (!secretKey || !secretKey.startsWith('sk_')) throw new Error('Sahay: pass your secret key (sk_...)')
    this.secretKey = secretKey
    this.host = host.replace(/\/$/, '')
    this.events = { send: (type, data, opts = {}) => this.request('POST', '/v1/api/events', { id: opts.id || `${type}:${data.orderRef || data.sku || crypto.randomUUID()}:${data.status || ''}`, type, data }) }
    this.tickets = { create: (body) => this.request('POST', '/v1/api/tickets', body), list: (q) => this.request('GET', '/v1/api/tickets?' + new URLSearchParams(q)) }
    this.leads = { create: (body) => this.request('POST', '/v1/api/leads', body) }
  }

  /** Sign a logged-in customer so the chat can show their orders. Pass the result to the widget's init(). */
  identify(user) {
    const value = String(user.email || user.phone || '').trim().toLowerCase()
    if (!value) throw new Error('Sahay.identify needs an email or phone')
    return { user, userHash: crypto.createHmac('sha256', this.secretKey).update(value).digest('hex') }
  }

  async request(method, path, body) {
    const r = await fetch(this.host + path, {
      method, headers: { Authorization: `Bearer ${this.secretKey}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(`Sahay ${r.status}: ${data.detail || r.statusText}`)
    return data
  }

  /** Check a request from Sahay (Commerce Contract call or webhook): signature over "<timestamp>.<raw body>". */
  verify(rawBody, headers) {
    const ts = headers['x-sahay-timestamp'], sig = headers['x-sahay-signature']
    if (!ts || !sig || Math.abs(Date.now() / 1000 - Number(ts)) > TOLERANCE_S) return false
    const want = 'sha256=' + crypto.createHmac('sha256', this.secretKey).update(`${ts}.${rawBody}`).digest('hex')
    return want.length === sig.length && crypto.timingSafeEqual(Buffer.from(want), Buffer.from(sig))
  }

  /** Verify our outgoing webhooks with the endpoint's own secret (whsec_...). */
  static verifyWebhook(rawBody, headers, webhookSecret) {
    const ts = headers['x-sahay-timestamp'], sig = headers['x-sahay-signature']
    if (!ts || !sig || Math.abs(Date.now() / 1000 - Number(ts)) > TOLERANCE_S) return false
    const want = 'sha256=' + crypto.createHmac('sha256', webhookSecret).update(`${ts}.${rawBody}`).digest('hex')
    return want.length === sig.length && crypto.timingSafeEqual(Buffer.from(want), Buffer.from(sig))
  }

  /**
   * Express/Connect/Node http handler for the Commerce Contract (level 3). Mount it BEFORE express.json()
   * (it needs the raw body to check the signature). Implement only the actions you support:
   *   getOrder({orderRef}) · findOrders({phone, email, limit}) · searchProducts({query, limit}) · getProduct({sku})
   *   checkStock({sku}) · createReturn({orderRef, skus, reason, type: 'return'|'exchange'}) · cancelOrder({orderRef, reason})
   *   refundOrder({orderRef, amount, reason}) · updateAddress({orderRef, address}) · rescheduleDelivery({orderRef, date})
   *   createCoupon({value, customerRef}) · tagOrder({orderRef, tag})
   * Orders use: { orderRef, customerName, customerPhone, customerEmail, items: [{sku, title, qty, price}], total, paymentMethod,
   *   status, courier, trackingNumber, trackingUrl, expectedDelivery, deliveredAt, placedAt }
   */
  handler(actions) {
    return async (req, res) => {
      const raw = typeof req.body === 'string' ? req.body : Buffer.isBuffer(req.body) ? req.body.toString('utf8') : await readBody(req)
      const send = (status, obj) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(obj)) }
      if (!this.verify(raw, lower(req.headers))) return send(401, { ok: false, error: 'bad_signature' })
      let msg
      try { msg = JSON.parse(raw) } catch { return send(400, { ok: false, error: 'bad_json' }) }
      if (msg.action === 'ping') return send(200, { ok: true, data: { actions: Object.keys(actions) } })
      const fn = actions[msg.action]
      if (!fn) return send(404, { ok: false, error: 'not_supported' })
      try { send(200, { ok: true, data: (await fn(msg.params || {})) ?? null }) } catch (e) { send(500, { ok: false, error: String(e.message || e) }) }
    }
  }
}

function lower(h) { const o = {}; for (const k in h) o[k.toLowerCase()] = Array.isArray(h[k]) ? h[k][0] : h[k]; return o }
function readBody(req) {
  return new Promise((resolve, reject) => { let d = ''; req.setEncoding('utf8'); req.on('data', (c) => (d += c)); req.on('end', () => resolve(d)); req.on('error', reject) })
}

export default Sahay

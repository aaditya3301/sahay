// Thin wrapper around the hosted loader: queues calls until /widget.js has loaded.
const DEFAULT_HOST = 'https://sahay-536440059442.asia-south1.run.app'

function queue() {
  if (typeof window === 'undefined') return null
  if (!window.Sahay) {
    const q = function () { (q.q = q.q || []).push(arguments) }
    window.Sahay = q
  }
  return window.Sahay
}

let loaded = false

export function init(options) {
  const S = queue()
  if (!S) return
  const host = (options.host || DEFAULT_HOST).replace(/\/$/, '')
  S('init', options)
  if (!loaded) {
    loaded = true
    const s = document.createElement('script')
    s.src = host + '/widget.js'
    s.async = true
    s.setAttribute('data-manual', '')
    document.head.appendChild(s)
  }
}

export const setContext = (context) => queue()?.('setContext', context)
export const identify = (user, userHash) => queue()?.('identify', { user, userHash })
export const open = () => queue()?.('open')
export const close = () => queue()?.('close')
export const on = (event, handler) => queue()?.('on', event, handler)
/** Report the cart so Sahay can send one reminder if checkout is abandoned. */
export const cart = (data) => queue()?.('cart', data)

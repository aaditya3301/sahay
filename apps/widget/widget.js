/* Sahay chat widget loader.
   Script tag:  <script src="https://<host>/widget.js" data-site-key="pk_..." async></script>
   API:         Sahay('init', { siteKey, user: { email, phone, name }, userHash, context: { productSku, cart } })
                Sahay('setContext', {...}) · Sahay('open') · Sahay('close') · Sahay('on', 'lead_captured', fn)
                Sahay('cart', { items: [{ sku, qty }], phone, email, name, checkoutUrl })  → abandoned-cart reminders
   The chat runs in an iframe served by Sahay, so the shop's CSS can't break it and the iframe only
   loads on domains the business allowed (CSP frame-ancestors). */
(function () {
  if (window.Sahay && window.Sahay.__loaded) return;
  var script = document.currentScript || document.querySelector('script[src*="widget.js"][data-site-key],script[src*="widget.js"][data-key]');
  var BASE = script ? new URL(script.src, location.href).origin : (window.SahayHost || location.origin);
  var queued = (window.Sahay && window.Sahay.q) || [];
  var state = { key: null, opts: {}, ctx: {}, frame: null, wrap: null, btn: null, ready: false, open: false, handlers: {}, cart: null };

  function autoContext() {
    var c = { page_url: location.href.split('#')[0], page_title: document.title };
    try { // Shopify product pages expose the product on window.ShopifyAnalytics
      var p = window.ShopifyAnalytics && window.ShopifyAnalytics.meta && window.ShopifyAnalytics.meta.product;
      if (p) c.product = { sku: p.variants && p.variants[0] && p.variants[0].sku, title: p.variants && p.variants[0] && p.variants[0].name };
    } catch (e) {}
    return c;
  }
  function norm(ctx) {
    var out = {};
    for (var k in ctx || {}) {
      var key = { productSku: 'product_sku', productTitle: 'product_title', pageUrl: 'page_url', pageTitle: 'page_title' }[k] || k;
      out[key] = ctx[k];
    }
    return out;
  }
  function post(msg) { if (state.frame && state.ready) state.frame.contentWindow.postMessage(msg, BASE); }
  function sendContext() { post({ type: 'sahay:context', context: Object.assign(autoContext(), state.ctx) }); }
  function emit(name, data) {
    (state.handlers[name] || []).forEach(function (fn) { try { fn(data); } catch (e) {} });
    try { window.dispatchEvent(new CustomEvent('sahay:' + name, { detail: data })); } catch (e) {}
  }

  var ICON_CHAT = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.9-.9L3 20.5l1.5-4.6A8.2 8.2 0 0 1 3 11.5 8.6 8.6 0 0 1 12 3a8.6 8.6 0 0 1 9 8.5Z"/></svg>';
  var ICON_CLOSE = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  // Launcher colours per chat theme (the chat itself is themed inside the iframe)
  var THEMES = {
    boutique: { bg: '#362c29', ink: '#fcf7f3', panel: '#fcf7f3', dot: '#e8590c' },
    heritage: { bg: '#203f2c', ink: '#f4f5ec', panel: '#f4f5ec', dot: '#9cc59a' }
  };

  function setOpen(v) {
    state.open = v;
    if (!state.wrap) return;
    state.wrap.classList.toggle('is-open', v);
    state.btn.classList.toggle('is-open', v);
    state.btn.innerHTML = v ? ICON_CLOSE : ICON_CHAT + '<span class="sl-t">' + state.label + '</span><i class="sl-dot"></i>';
    state.btn.setAttribute('aria-label', v ? 'Close chat' : state.label);
    if (v) { post({ type: 'sahay:opened' }); sendContext(); }
  }

  function mount(cfg) {
    var color = cfg.brand_color || '#4f46e5';
    var t = THEMES[cfg.theme] || { bg: color, ink: '#fff', panel: '#fff', dot: 'transparent' };
    state.label = String(cfg.launcher_text || 'Chat with us').replace(/[<>&"]/g, '');
    var btn = document.createElement('button');
    btn.id = 'sahay-launcher';
    btn.type = 'button';
    btn.title = state.label;
    var css = document.createElement('style');
    css.id = 'sahay-style';
    css.textContent =
      '#sahay-launcher{position:fixed;right:22px;bottom:22px;z-index:2147483000;display:inline-flex;align-items:center;gap:10px;height:56px;padding:0 22px 0 18px;' +
      'border:0;border-radius:999px;cursor:pointer;font:600 15px/1 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;letter-spacing:-.01em;' +
      'color:' + t.ink + ';background:' + t.bg + ';box-shadow:0 12px 32px -8px rgba(0,0,0,.35),0 2px 6px rgba(0,0,0,.12);transition:transform .25s cubic-bezier(.16,1,.3,1),box-shadow .25s}' +
      '#sahay-launcher:hover{transform:translateY(-2px);box-shadow:0 16px 36px -8px rgba(0,0,0,.4),0 2px 6px rgba(0,0,0,.12)}' +
      '#sahay-launcher:focus-visible{outline:3px solid ' + t.bg + ';outline-offset:3px}' +
      '#sahay-launcher .sl-dot{position:absolute;top:13px;left:33px;width:8px;height:8px;border-radius:50%;background:' + t.dot + ';box-shadow:0 0 0 2px ' + t.bg + '}' +
      '#sahay-launcher.is-open{width:56px;padding:0;justify-content:center}' +
      '#sahay-frame-wrap{position:fixed;right:22px;bottom:90px;width:384px;max-width:calc(100vw - 24px);height:620px;max-height:calc(100vh - 112px);z-index:2147483000;' +
      'border-radius:22px;overflow:hidden;background:' + t.panel + ';box-shadow:0 30px 80px -20px rgba(20,15,10,.38),0 0 0 1px rgba(0,0,0,.06);display:none;transform-origin:bottom right}' +
      '#sahay-frame-wrap.is-open{display:block;animation:sahay-in .32s cubic-bezier(.16,1,.3,1)}' +
      '@keyframes sahay-in{from{opacity:0;transform:translateY(14px) scale(.97)}to{opacity:1;transform:none}}' +
      '@media (prefers-reduced-motion:reduce){#sahay-frame-wrap.is-open{animation:none}}' +
      '@media (max-width:480px){#sahay-launcher{right:16px;bottom:16px;width:56px;padding:0;justify-content:center}#sahay-launcher .sl-t{display:none}#sahay-launcher.is-open{display:none}' +
      '#sahay-frame-wrap{right:0;bottom:0;width:100vw;max-width:100vw;height:100%;max-height:100%;border-radius:0}}';
    document.head.appendChild(css);
    var wrap = document.createElement('div');
    wrap.id = 'sahay-frame-wrap';
    var frame = document.createElement('iframe');
    frame.title = (cfg.name || 'Shop') + ' chat';
    frame.src = BASE + '/w/' + encodeURIComponent(state.key) + '?origin=' + encodeURIComponent(location.origin) +
      '&theme=' + encodeURIComponent(cfg.theme || 'classic') + '&c=' + encodeURIComponent(color);
    frame.allow = 'clipboard-write';
    frame.style.cssText = 'width:100%;height:100%;border:0';
    wrap.appendChild(frame);
    document.body.appendChild(wrap);
    document.body.appendChild(btn);
    state.btn = btn; state.wrap = wrap; state.frame = frame;
    btn.onclick = function () { setOpen(!state.open); };
    setOpen(state.open);
  }

  window.addEventListener('message', function (e) {
    if (e.origin !== BASE || !e.data || typeof e.data.type !== 'string') return;
    var d = e.data;
    if (d.type === 'sahay:ready') {
      state.ready = true;
      post({ type: 'sahay:init', user: state.opts.user || null, userHash: state.opts.userHash || null, context: Object.assign(autoContext(), state.ctx) });
      if (state.cart) post({ type: 'sahay:cart', cart: state.cart });
      if (state.opts.open) setOpen(true);
    } else if (d.type === 'sahay:close') setOpen(false);
    else if (d.type === 'sahay:event') emit(d.name, d.data);
  });

  function init(opts) {
    if (state.key) return;
    state.opts = opts || {};
    state.key = state.opts.siteKey || state.opts.key;
    state.ctx = norm(state.opts.context);
    if (!state.key) { console.warn('[Sahay] missing siteKey'); return; }
    fetch(BASE + '/v1/widget/config?key=' + encodeURIComponent(state.key))
      .then(function (r) { if (!r.ok) throw new Error('unknown site key'); return r.json(); })
      .then(function (cfg) {
        if (document.body) mount(cfg); else document.addEventListener('DOMContentLoaded', function () { mount(cfg); });
      })
      .catch(function (err) { console.warn('[Sahay] widget not loaded:', err.message); });
  }

  var api = function (cmd, arg, fn) {
    if (cmd === 'init') init(arg);
    else if (cmd === 'setContext') { state.ctx = Object.assign(state.ctx, norm(arg)); sendContext(); }
    else if (cmd === 'open') setOpen(true);
    else if (cmd === 'close') setOpen(false);
    else if (cmd === 'on') { (state.handlers[arg] = state.handlers[arg] || []).push(fn); }
    else if (cmd === 'cart') { state.cart = arg || {}; post({ type: 'sahay:cart', cart: state.cart }); }
    else if (cmd === 'identify') { state.opts.user = arg && arg.user; state.opts.userHash = arg && arg.userHash; post({ type: 'sahay:init', user: state.opts.user, userHash: state.opts.userHash, context: Object.assign(autoContext(), state.ctx) }); }
  };
  api.__loaded = true;
  window.Sahay = api;
  queued.forEach(function (args) { api.apply(null, args); });

  var key = script && (script.getAttribute('data-site-key') || script.getAttribute('data-key'));
  if (key && script.getAttribute('data-manual') == null) init({ siteKey: key });
})();

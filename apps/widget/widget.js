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

  function setOpen(v) {
    state.open = v;
    if (!state.wrap) return;
    state.wrap.style.display = v ? 'block' : 'none';
    state.btn.innerHTML = v ? '&#10005;' : '&#128172;';
    state.btn.setAttribute('aria-label', v ? 'Close chat' : 'Chat with us');
    if (v) { post({ type: 'sahay:opened' }); sendContext(); }
  }

  function mount(cfg) {
    var color = cfg.brand_color || '#4f46e5';
    var btn = document.createElement('button');
    btn.id = 'sahay-launcher';
    btn.setAttribute('aria-label', 'Chat with us');
    btn.title = cfg.launcher_text || 'Chat with us';
    btn.innerHTML = '&#128172;';
    btn.style.cssText = 'position:fixed;right:22px;bottom:22px;width:58px;height:58px;border-radius:50%;border:0;cursor:pointer;z-index:2147483000;' +
      'font-size:26px;color:#fff;background:' + color + ';box-shadow:0 8px 24px rgba(0,0,0,.25)';
    var css = document.createElement('style');
    css.id = 'sahay-style';
    css.textContent = '#sahay-frame-wrap{position:fixed;right:22px;bottom:92px;width:380px;max-width:calc(100vw - 24px);height:600px;' +
      'max-height:calc(100vh - 110px);z-index:2147483000;border-radius:16px;overflow:hidden;box-shadow:0 16px 48px rgba(0,0,0,.25);display:none;background:#fff}' +
      '@media (max-width:480px){#sahay-frame-wrap{right:0;bottom:0;width:100vw;max-width:100vw;height:100%;max-height:100%;border-radius:0}}';
    document.head.appendChild(css);
    var wrap = document.createElement('div');
    wrap.id = 'sahay-frame-wrap';
    var frame = document.createElement('iframe');
    frame.title = (cfg.name || 'Shop') + ' chat';
    frame.src = BASE + '/w/' + encodeURIComponent(state.key) + '?origin=' + encodeURIComponent(location.origin);
    frame.allow = 'clipboard-write';
    frame.style.cssText = 'width:100%;height:100%;border:0';
    wrap.appendChild(frame);
    document.body.appendChild(wrap);
    document.body.appendChild(btn);
    state.btn = btn; state.wrap = wrap; state.frame = frame;
    btn.onclick = function () { setOpen(!state.open); };
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

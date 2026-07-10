/*
 * Embeddable chat widget for the multi-tenant assistant framework.
 * Usage:  <script src="https://YOUR_BACKEND/widget.js" data-slug="nexus"></script>
 *
 * Self-contained: derives the API base from its own <script> src origin, then
 * talks directly to the scoped public API (/api/public/{slug}/...). No build,
 * no external deps, no iframe.
 */
(function () {
  var script = document.currentScript;
  if (!script) {
    var all = document.getElementsByTagName('script');
    script = all[all.length - 1];
  }
  var slug = script.getAttribute('data-slug');
  if (!slug) { console.error('[chat-widget] missing data-slug'); return; }

  var apiBase = new URL(script.src).origin;
  var history = [];
  var accent = '#4f46e5';
  var botName = 'Assistant';
  var open = false;

  // ---- styles ----
  var css = document.createElement('style');
  css.textContent = [
    '.cw-btn{position:fixed;bottom:20px;right:20px;width:60px;height:60px;border-radius:50%;',
    'border:none;cursor:pointer;color:#fff;font-size:26px;box-shadow:0 6px 18px rgba(0,0,0,.25);z-index:2147483000;}',
    '.cw-panel{position:fixed;bottom:90px;right:20px;width:360px;max-width:calc(100vw - 40px);height:520px;',
    'max-height:calc(100vh - 120px);background:#fff;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.3);',
    'display:none;flex-direction:column;overflow:hidden;z-index:2147483000;font-family:system-ui,Arial,sans-serif;}',
    '.cw-panel.open{display:flex;}',
    '.cw-head{padding:14px 16px;color:#fff;font-weight:600;}',
    '.cw-msgs{flex:1;overflow-y:auto;padding:14px;background:#f7f7f9;}',
    '.cw-m{margin:8px 0;padding:9px 12px;border-radius:12px;max-width:80%;font-size:14px;line-height:1.4;white-space:pre-wrap;}',
    '.cw-user{background:#4f46e5;color:#fff;margin-left:auto;border-bottom-right-radius:3px;}',
    '.cw-bot{background:#fff;color:#111;border:1px solid #e5e7eb;border-bottom-left-radius:3px;}',
    '.cw-foot{display:flex;border-top:1px solid #eee;}',
    '.cw-foot input{flex:1;border:none;padding:13px;font-size:14px;outline:none;}',
    '.cw-foot button{border:none;background:none;padding:0 14px;font-size:18px;cursor:pointer;color:#4f46e5;}'
  ].join('');
  document.head.appendChild(css);

  var btn = document.createElement('button');
  btn.className = 'cw-btn';
  btn.innerHTML = '💬';

  var panel = document.createElement('div');
  panel.className = 'cw-panel';
  panel.innerHTML =
    '<div class="cw-head"></div>' +
    '<div class="cw-msgs"></div>' +
    '<div class="cw-foot"><input type="text" placeholder="Type a message..."/><button>➤</button></div>';

  document.body.appendChild(btn);
  document.body.appendChild(panel);

  var head = panel.querySelector('.cw-head');
  var msgs = panel.querySelector('.cw-msgs');
  var input = panel.querySelector('input');
  var send = panel.querySelector('button');

  function addMsg(text, who) {
    var d = document.createElement('div');
    d.className = 'cw-m ' + (who === 'user' ? 'cw-user' : 'cw-bot');
    d.textContent = text;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  function applyBranding(cfg) {
    accent = cfg.accent_color || accent;
    botName = cfg.bot_name || cfg.name || botName;
    btn.style.background = accent;
    head.style.background = accent;
    head.textContent = botName;
    if (cfg.greeting) addMsg(cfg.greeting, 'bot');
  }

  // Load branding config
  fetch(apiBase + '/api/public/' + encodeURIComponent(slug) + '/config')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (cfg) { if (cfg) applyBranding(cfg); else { head.textContent = botName; } })
    .catch(function () { head.textContent = botName; });

  function toggle() {
    open = !open;
    panel.classList.toggle('open', open);
    if (open) input.focus();
  }
  btn.addEventListener('click', toggle);

  function submit() {
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMsg(text, 'user');
    var typing = addMsg('…', 'bot');
    fetch(apiBase + '/api/public/' + encodeURIComponent(slug) + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history: history })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var reply = data.response || "Sorry, I couldn't process that.";
        typing.textContent = reply;
        history.push({ role: 'user', content: text });
        history.push({ role: 'assistant', content: reply });
      })
      .catch(function () { typing.textContent = 'Connection error. Please try again.'; });
  }

  send.addEventListener('click', submit);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
})();

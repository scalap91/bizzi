/*!
 * bizzi-loader.js — Loader universel Bizzi
 * Servi par https://bizzi.fr/bizzi-loader.js
 *
 * Architecture :
 *  - Vanilla JS pur, zéro dépendance, 1 seul fichier.
 *  - Pattern : <div data-bizzi-mount="{type}" data-bizzi-tenant="{slug}"></div>
 *  - Renderers indexés par type. `chat` est livré built-in.
 *  - Extensible : window.Bizzi.registerRenderer(type, fn).
 *  - CSS scopé `.bzz-*` pour ne jamais polluer le tenant.
 *
 * Usage minimal (côté tenant) :
 *  <script src="https://bizzi.fr/bizzi-loader.js" defer></script>
 *  <div data-bizzi-mount="chat" data-bizzi-tenant="airbizness"></div>
 */
(function () {
  'use strict';

  // ====================================================================
  // 0. Config & helpers globaux
  // ====================================================================
  var DEFAULT_API = 'https://bizzi.fr/api';
  var DEFAULT_PRIMARY_COLOR = '#2d6a4f';
  var HISTORY_CAP = 50;
  var STYLE_ID = 'bzz-styles';

  var RENDERERS = {};

  // UUID v4 light (pour session_id)
  function uuid() {
    return ('10000000-1000-4000-8000-100000000000').replace(/[018]/g, function (c) {
      return (c ^ (Math.random() * 16) >> (c / 4)).toString(16);
    });
  }

  function $(html) {
    var t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Markdown léger : **bold**, *italic*, [text](url), \n -> <br>
  function lightMarkdown(s) {
    var out = escapeHtml(s);
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*])\*([^*]+)\*([^*]|$)/g, '$1<em>$2</em>$3');
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    out = out.replace(/\n/g, '<br>');
    return out;
  }

  function readOpts(el) {
    return {
      type: el.getAttribute('data-bizzi-mount') || 'chat',
      tenant: el.getAttribute('data-bizzi-tenant') || 'default',
      api: el.getAttribute('data-bizzi-api') || DEFAULT_API,
      title: el.getAttribute('data-bizzi-title') || '',
      color: el.getAttribute('data-bizzi-color') || DEFAULT_PRIMARY_COLOR
    };
  }

  // ====================================================================
  // 1. Styles scopés (.bzz-*) injectés une seule fois
  // ====================================================================
  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      ':root{--bzz-primary:' + DEFAULT_PRIMARY_COLOR + ';}',
      '.bzz-chat-bubble{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:var(--bzz-primary);color:#fff;border:none;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.18);display:flex;align-items:center;justify-content:center;z-index:2147483000;transition:transform .18s ease, box-shadow .18s ease;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}',
      '.bzz-chat-bubble:hover{transform:scale(1.05);box-shadow:0 8px 24px rgba(0,0,0,.22);}',
      '.bzz-chat-bubble svg{width:26px;height:26px;fill:none;stroke:#fff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}',
      '.bzz-chat-panel{position:fixed;bottom:96px;right:24px;width:360px;height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.25);display:flex;flex-direction:column;overflow:hidden;z-index:2147483001;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#222;animation:bzz-pop-in .22s ease-out;}',
      '@keyframes bzz-pop-in{from{opacity:0;transform:translateY(12px) scale(.97);}to{opacity:1;transform:none;}}',
      '@media (max-width:600px){.bzz-chat-panel{right:0;bottom:0;width:100vw;height:100vh;max-height:100vh;border-radius:0;}}',
      '.bzz-chat-header{background:var(--bzz-primary);color:#fff;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;}',
      '.bzz-chat-title{font-size:15px;font-weight:600;margin:0;line-height:1.2;}',
      '.bzz-chat-close{background:transparent;border:none;color:#fff;cursor:pointer;font-size:22px;line-height:1;padding:4px 8px;border-radius:6px;}',
      '.bzz-chat-close:hover{background:rgba(255,255,255,.15);}',
      '.bzz-chat-body{flex:1;overflow-y:auto;padding:14px 14px 6px 14px;background:#f7f9f8;display:flex;flex-direction:column;gap:8px;}',
      '.bzz-msg{max-width:82%;padding:9px 12px;border-radius:14px;font-size:14px;line-height:1.4;word-wrap:break-word;white-space:normal;}',
      '.bzz-msg-user{align-self:flex-end;background:var(--bzz-primary);color:#fff;border-bottom-right-radius:4px;}',
      '.bzz-msg-agent{align-self:flex-start;background:#e9ecef;color:#222;border-bottom-left-radius:4px;}',
      '.bzz-msg-error{align-self:flex-start;background:#fde2e2;color:#a82020;border-bottom-left-radius:4px;}',
      '.bzz-msg a{color:inherit;text-decoration:underline;}',
      '.bzz-typing{align-self:flex-start;background:#e9ecef;color:#666;padding:9px 14px;border-radius:14px;border-bottom-left-radius:4px;font-size:14px;display:inline-flex;gap:3px;}',
      '.bzz-typing span{width:6px;height:6px;background:#888;border-radius:50%;animation:bzz-blink 1.2s infinite;}',
      '.bzz-typing span:nth-child(2){animation-delay:.2s;}',
      '.bzz-typing span:nth-child(3){animation-delay:.4s;}',
      '@keyframes bzz-blink{0%,80%,100%{opacity:.3;}40%{opacity:1;}}',
      '.bzz-chat-footer{border-top:1px solid #e5e7eb;padding:10px;display:flex;gap:8px;align-items:flex-end;background:#fff;flex-shrink:0;}',
      '.bzz-chat-input{flex:1;border:1px solid #d1d5db;border-radius:10px;padding:8px 10px;font-size:14px;font-family:inherit;resize:none;outline:none;line-height:1.4;max-height:72px;overflow-y:auto;}',
      '.bzz-chat-input:focus{border-color:var(--bzz-primary);box-shadow:0 0 0 2px rgba(45,106,79,.18);}',
      '.bzz-chat-btn{background:var(--bzz-primary);color:#fff;border:none;border-radius:10px;width:38px;height:38px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;}',
      '.bzz-chat-btn:disabled{opacity:.5;cursor:not-allowed;}',
      '.bzz-chat-btn svg{width:18px;height:18px;fill:none;stroke:#fff;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;}',
      '.bzz-chat-mic{background:transparent;color:#666;border:1px solid #d1d5db;}',
      '.bzz-chat-mic svg{stroke:#555;}',
      '.bzz-chat-mic:hover{background:#f3f4f6;}',
      '.bzz-hidden{display:none !important;}'
    ].join('\n');
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ====================================================================
  // 2. Renderer "chat" — built-in
  // ====================================================================
  RENDERERS.chat = function renderChat(mountEl, opts) {
    ensureStyles();

    var tenant = opts.tenant;
    var apiBase = opts.api.replace(/\/+$/, '');
    var primary = opts.color;

    var sessionKey = 'bzz-session-' + tenant;
    var historyKey = 'bzz-history-' + tenant;

    var sessionId;
    try {
      sessionId = localStorage.getItem(sessionKey) || uuid();
      localStorage.setItem(sessionKey, sessionId);
    } catch (e) {
      sessionId = uuid();
    }

    var loadedHistory = false;
    var sending = false;
    var titleStr = opts.title || ('Conseiller ' + tenant);

    // Bouton flottant
    var bubble = $(
      '<button type="button" class="bzz-chat-bubble" aria-label="Ouvrir le chat" title="Ouvrir le chat">' +
        '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 1 1-3.6-6.7L21 4l-1 4.5A8 8 0 0 1 21 12z"/></svg>' +
      '</button>'
    );
    bubble.style.setProperty('--bzz-primary', primary);

    // Panneau
    var panel = $(
      '<section class="bzz-chat-panel bzz-hidden" role="dialog" aria-modal="false" aria-label="Fenêtre de chat">' +
        '<header class="bzz-chat-header">' +
          '<h3 class="bzz-chat-title"></h3>' +
          '<button type="button" class="bzz-chat-close" aria-label="Fermer">&times;</button>' +
        '</header>' +
        '<div class="bzz-chat-body" role="log" aria-live="polite"></div>' +
        '<form class="bzz-chat-footer" autocomplete="off">' +
          '<button type="button" class="bzz-chat-btn bzz-chat-mic" aria-label="Vocal (bientôt)">' +
            '<svg viewBox="0 0 24 24"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>' +
          '</button>' +
          '<textarea class="bzz-chat-input" rows="1" placeholder="Écrivez votre message…" aria-label="Votre message"></textarea>' +
          '<button type="submit" class="bzz-chat-btn bzz-chat-send" aria-label="Envoyer">' +
            '<svg viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>' +
          '</button>' +
        '</form>' +
      '</section>'
    );
    panel.style.setProperty('--bzz-primary', primary);
    panel.querySelector('.bzz-chat-title').textContent = titleStr;

    var bodyEl = panel.querySelector('.bzz-chat-body');
    var inputEl = panel.querySelector('.bzz-chat-input');
    var sendBtn = panel.querySelector('.bzz-chat-send');
    var micBtn = panel.querySelector('.bzz-chat-mic');
    var closeBtn = panel.querySelector('.bzz-chat-close');
    var formEl = panel.querySelector('form');

    mountEl.appendChild(bubble);
    mountEl.appendChild(panel);

    // ----- Helpers UI -----
    function appendMessage(role, text, opts2) {
      opts2 = opts2 || {};
      var cls = 'bzz-msg ';
      if (role === 'user') cls += 'bzz-msg-user';
      else if (role === 'error') cls += 'bzz-msg-error';
      else cls += 'bzz-msg-agent';
      var bubbleEl = document.createElement('div');
      bubbleEl.className = cls;
      bubbleEl.innerHTML = lightMarkdown(text);
      bodyEl.appendChild(bubbleEl);
      bodyEl.scrollTop = bodyEl.scrollHeight;
      if (!opts2.skipPersist) {
        persist(role, text);
      }
      return bubbleEl;
    }

    function showTyping() {
      var t = document.createElement('div');
      t.className = 'bzz-typing';
      t.setAttribute('data-bzz-typing', '1');
      t.innerHTML = '<span></span><span></span><span></span>';
      bodyEl.appendChild(t);
      bodyEl.scrollTop = bodyEl.scrollHeight;
      return t;
    }

    function hideTyping(el) {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }

    // ----- Persistance -----
    function persist(role, text) {
      if (role === 'error') return; // on ne persiste pas les erreurs réseau
      try {
        var raw = localStorage.getItem(historyKey);
        var arr = raw ? JSON.parse(raw) : [];
        arr.push({ role: role, text: text, ts: Date.now() });
        if (arr.length > HISTORY_CAP) arr = arr.slice(-HISTORY_CAP);
        localStorage.setItem(historyKey, JSON.stringify(arr));
      } catch (e) { /* quota / private mode */ }
    }

    function restoreHistory() {
      try {
        var raw = localStorage.getItem(historyKey);
        if (!raw) return false;
        var arr = JSON.parse(raw);
        if (!Array.isArray(arr) || !arr.length) return false;
        arr.forEach(function (m) {
          appendMessage(m.role, m.text, { skipPersist: true });
        });
        return true;
      } catch (e) { return false; }
    }

    // ----- Premier message (greeting) -----
    function loadGreeting() {
      var fallback = 'Bonjour ! Comment puis-je vous aider ?';
      fetch(apiBase + '/tenant/' + encodeURIComponent(tenant) + '/greeting', {
        method: 'GET',
        credentials: 'omit'
      }).then(function (r) {
        if (!r.ok) throw new Error('no greeting');
        return r.json();
      }).then(function (data) {
        var msg = (data && (data.greeting || data.message)) || fallback;
        appendMessage('agent', msg);
      }).catch(function () {
        appendMessage('agent', fallback);
      });
    }

    // ----- Charger info tenant (titre dynamique) -----
    function loadTenantInfo() {
      if (opts.title) return; // priorité au data-bizzi-title si fourni
      fetch(apiBase + '/tenant/' + encodeURIComponent(tenant) + '/info', {
        method: 'GET', credentials: 'omit'
      }).then(function (r) {
        if (!r.ok) throw new Error('no info');
        return r.json();
      }).then(function (data) {
        if (data && data.name) {
          panel.querySelector('.bzz-chat-title').textContent = 'Conseiller ' + data.name;
        }
      }).catch(function () { /* silencieux */ });
    }

    // ----- Envoi message -----
    function sendMessage(text) {
      if (sending) return;
      text = (text || '').trim();
      if (!text) return;
      sending = true;
      sendBtn.disabled = true;
      appendMessage('user', text);
      inputEl.value = '';
      autosize();

      var typing = showTyping();

      fetch(apiBase + '/tools/chat/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'omit',
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          tenant: tenant
        })
      }).then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      }).then(function (res) {
        hideTyping(typing);
        if (!res.ok) throw new Error('http');
        var data = res.data || {};
        // tolérance plusieurs formats : response | reply | message | text
        var reply = data.response || data.reply || data.message || data.text;
        if (typeof reply !== 'string' || !reply) {
          reply = 'Désolé, je n\'ai pas reçu de réponse exploitable.';
        }
        if (data.session_id) {
          sessionId = data.session_id;
          try { localStorage.setItem(sessionKey, sessionId); } catch (e) {}
        }
        appendMessage('agent', reply);
      }).catch(function () {
        hideTyping(typing);
        appendMessage('error', 'Désolé, problème technique. Réessayez dans 1 minute.');
      }).then(function () {
        sending = false;
        sendBtn.disabled = false;
        inputEl.focus();
      });
    }

    // ----- Auto-resize input (max 3 lignes) -----
    function autosize() {
      inputEl.style.height = 'auto';
      var max = 72; // ≈ 3 lignes
      inputEl.style.height = Math.min(inputEl.scrollHeight, max) + 'px';
    }

    // ----- Ouverture / fermeture -----
    function open() {
      panel.classList.remove('bzz-hidden');
      bubble.classList.add('bzz-hidden');
      if (!loadedHistory) {
        loadedHistory = true;
        loadTenantInfo();
        var had = restoreHistory();
        if (!had) loadGreeting();
      }
      setTimeout(function () { inputEl.focus(); }, 50);
    }

    function close() {
      panel.classList.add('bzz-hidden');
      bubble.classList.remove('bzz-hidden');
      bubble.focus();
    }

    // ----- Bindings -----
    bubble.addEventListener('click', open);
    closeBtn.addEventListener('click', close);
    micBtn.addEventListener('click', function () {
      appendMessage('agent', 'Bientôt disponible : la saisie vocale arrive prochainement.', { skipPersist: true });
    });

    formEl.addEventListener('submit', function (e) {
      e.preventDefault();
      sendMessage(inputEl.value);
    });

    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(inputEl.value);
      }
    });

    inputEl.addEventListener('input', autosize);

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !panel.classList.contains('bzz-hidden')) close();
    });
  };

  // ====================================================================
  // 2bis. Renderers iframe — `bureau` et `meeting`
  // ====================================================================
  function ensureIframeStyles() {
    if (document.getElementById('bzz-iframe-styles')) return;
    var css = [
      '.bzz-bureau-iframe,.bzz-meeting-iframe{width:100%;min-height:600px;border:1px solid #ddd;border-radius:8px;display:block;}'
    ].join('\n');
    var style = document.createElement('style');
    style.id = 'bzz-iframe-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function makeIframeRenderer(pagePath, klass) {
    return function (mountEl, opts) {
      ensureIframeStyles();
      var tenant = encodeURIComponent(opts.tenant || 'default');
      var height = mountEl.getAttribute('data-bizzi-height') || '600';
      var iframe = document.createElement('iframe');
      iframe.className = klass;
      iframe.src = 'https://bizzi.fr' + pagePath + '?tenant=' + tenant;
      iframe.setAttribute('width', '100%');
      iframe.setAttribute('height', String(height));
      iframe.setAttribute('frameborder', '0');
      iframe.setAttribute('allow', 'clipboard-write');
      iframe.setAttribute('loading', 'lazy');
      iframe.style.minHeight = String(height) + 'px';
      mountEl.appendChild(iframe);
    };
  }

  RENDERERS.bureau  = makeIframeRenderer('/bureau.html',             'bzz-bureau-iframe');
  RENDERERS.meeting = makeIframeRenderer('/bizzi-meeting-room.html', 'bzz-meeting-iframe');

  // ====================================================================
  // 3. API publique : registerRenderer + mountAll
  // ====================================================================
  var Bizzi = window.Bizzi || {};
  Bizzi.version = '0.1.0';
  Bizzi.registerRenderer = function (type, fn) {
    if (typeof type !== 'string' || typeof fn !== 'function') return;
    RENDERERS[type] = fn;
  };
  Bizzi.mountAll = function (root) {
    root = root || document;
    var nodes = root.querySelectorAll('[data-bizzi-mount]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.getAttribute('data-bzz-mounted') === '1') continue;
      var opts = readOpts(el);
      var fn = RENDERERS[opts.type];
      if (!fn) {
        console.warn('[Bizzi] renderer non trouvé pour type=', opts.type);
        continue;
      }
      try {
        fn(el, opts);
        el.setAttribute('data-bzz-mounted', '1');
      } catch (err) {
        console.error('[Bizzi] mount failed for', opts.type, err);
      }
    }
  };
  window.Bizzi = Bizzi;

  // ====================================================================
  // 4. Auto-mount au DOMContentLoaded
  // ====================================================================
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { Bizzi.mountAll(); });
  } else {
    Bizzi.mountAll();
  }
})();

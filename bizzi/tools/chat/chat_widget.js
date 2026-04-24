/**
 * core-agents chat widget — 1 ligne à coller sur n'importe quel site
 *
 * <script src="https://api.core-agents.fr/widget.js"
 *         data-tenant="mon-org"
 *         data-token="VOTRE_TOKEN"
 *         data-color="#e02d2d"
 *         data-name="Mon Organisation">
 * </script>
 */
(function () {
  const s      = document.currentScript;
  const TOKEN  = s?.getAttribute('data-token')  || '';
  const COLOR  = s?.getAttribute('data-color')  || '#e02d2d';
  const NAME   = s?.getAttribute('data-name')   || 'Nous';
  const TENANT = s?.getAttribute('data-tenant') || 'default';
  const API    = s?.getAttribute('data-api')    || 'https://api.core-agents.fr';
  const SID    = 'ca_' + Math.random().toString(36).slice(2);
  let open = false;

  const css = document.createElement('style');
  css.textContent = `
#ca{position:fixed;right:20px;bottom:20px;z-index:9999;font-family:'Outfit',sans-serif}
#ca-btn{width:54px;height:54px;border-radius:50%;background:${COLOR};border:none;cursor:pointer;font-size:1.4rem;box-shadow:0 4px 16px rgba(0,0,0,.3);transition:transform .2s}
#ca-btn:hover{transform:scale(1.08)}
#ca-box{position:absolute;right:0;bottom:66px;width:330px;background:#0f1222;border:1px solid #1e2438;border-radius:13px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.4);display:none;flex-direction:column}
#ca-box.on{display:flex;animation:cain .18s ease}
@keyframes cain{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
#ca-hd{background:${COLOR};padding:13px 15px;display:flex;align-items:center;gap:9px}
#ca-hd-name{font-weight:700;color:#fff;font-size:.83rem}
#ca-hd-status{font-size:.62rem;color:rgba(255,255,255,.7)}
#ca-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:7px;min-height:180px;max-height:300px}
#ca-msgs::-webkit-scrollbar{width:2px}#ca-msgs::-webkit-scrollbar-thumb{background:#1e2438}
.ca-m{display:flex;gap:6px;animation:cain .15s ease}
.ca-m.u{flex-direction:row-reverse}
.ca-b{padding:8px 11px;border-radius:0 8px 8px 8px;max-width:82%;font-size:.74rem;line-height:1.55;color:#eef0f8;background:#151929;border:1px solid #1e2438}
.ca-m.u .ca-b{border-radius:8px 0 8px 8px;background:${COLOR}22;border-color:${COLOR}44}
.ca-acts{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.ca-act{font-size:.63rem;padding:3px 7px;border-radius:4px;background:#151929;border:1px solid #1e2438;color:#52587a;cursor:pointer;transition:all .12s}
.ca-act:hover{color:#eef0f8;border-color:${COLOR}}
.ca-typing{display:flex;gap:4px;padding:8px 11px}
.ca-typing span{width:5px;height:5px;border-radius:50%;background:#52587a;animation:cadot 1.2s infinite}
.ca-typing span:nth-child(2){animation-delay:.2s}.ca-typing span:nth-child(3){animation-delay:.4s}
@keyframes cadot{0%,80%,100%{opacity:.3}40%{opacity:1}}
#ca-inp-row{display:flex;gap:5px;padding:9px;border-top:1px solid #1e2438;background:#090b16}
#ca-inp{flex:1;background:#0d1020;border:1px solid #1e2438;border-radius:6px;padding:7px 10px;color:#eef0f8;font-size:.77rem;outline:none;font-family:inherit}
#ca-inp:focus{border-color:${COLOR}}
#ca-send{background:${COLOR};border:none;border-radius:6px;padding:7px 13px;color:#fff;cursor:pointer;font-size:.78rem;font-weight:700}
#ca-send:hover{opacity:.85}`;
  document.head.appendChild(css);

  const el = document.createElement('div');
  el.id = 'ca';
  el.innerHTML = `
<div id="ca-box">
  <div id="ca-hd">
    <div style="width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center">🤖</div>
    <div style="flex:1"><div id="ca-hd-name">${NAME}</div><div id="ca-hd-status">● En ligne</div></div>
    <button onclick="W.close()" style="background:transparent;border:none;color:rgba(255,255,255,.6);cursor:pointer;font-size:.95rem">✕</button>
  </div>
  <div id="ca-msgs"></div>
  <div id="ca-inp-row">
    <input id="ca-inp" placeholder="Votre message..." onkeydown="if(event.key==='Enter')W.send()">
    <button id="ca-send" onclick="W.send()">→</button>
  </div>
</div>
<button id="ca-btn" onclick="W.toggle()">💬</button>`;
  document.body.appendChild(el);

  window.W = {
    toggle(){ open ? this.close() : this.open(); },
    open(){
      open = true;
      document.getElementById('ca-box').classList.add('on');
      const msgs = document.getElementById('ca-msgs');
      if (!msgs.children.length)
        this.add('a', `Bonjour 👋 Je suis l'assistant de ${NAME}. Comment puis-je vous aider ?`, []);
      setTimeout(() => document.getElementById('ca-inp')?.focus(), 80);
    },
    close(){ open = false; document.getElementById('ca-box').classList.remove('on'); },
    add(role, text, acts=[]){
      const f = document.getElementById('ca-msgs');
      const d = document.createElement('div');
      d.className = `ca-m${role==='u'?' u':''}`;
      d.innerHTML = `<div class="ca-b">${text}${acts.length?`<div class="ca-acts">${acts.map(a=>`<button class="ca-act" onclick="W.quick('${a}')">${a}</button>`).join('')}</div>`:''}</div>`;
      f.appendChild(d); f.scrollTop = f.scrollHeight;
    },
    typing(show){
      document.getElementById('ca-t')?.remove();
      if (!show) return;
      const f = document.getElementById('ca-msgs');
      const d = document.createElement('div');
      d.id = 'ca-t'; d.className = 'ca-m';
      d.innerHTML = '<div class="ca-typing"><span></span><span></span><span></span></div>';
      f.appendChild(d); f.scrollTop = f.scrollHeight;
    },
    async send(){
      const inp = document.getElementById('ca-inp');
      const msg = inp?.value.trim(); if (!msg) return;
      inp.value = ''; this.add('u', msg);
      this.typing(true);
      try {
        const r = await fetch(`${API}/api/tools/chat/message`, {
          method:'POST',
          headers:{'Content-Type':'application/json','Authorization':`Bearer ${TOKEN}`},
          body: JSON.stringify({session_id:SID, message:msg, tenant:TENANT}),
        });
        const d = await r.json();
        this.typing(false);
        this.add('a', d.response||'Désolé, une erreur est survenue.', d.suggested_actions||[]);
        if (d.needs_human) setTimeout(()=>this.add('a','👤 Transfert vers un conseiller en cours...',[]),800);
      } catch { this.typing(false); this.add('a','Erreur technique. Contactez-nous directement.',[]);}
    },
    quick(t){ document.getElementById('ca-inp').value=t; this.send(); }
  };
})();

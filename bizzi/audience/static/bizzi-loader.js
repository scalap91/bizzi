/*!
 * bizzi-loader.js — script distribuable, ZÉRO dépendance.
 *
 * Pattern Pascal : aucun nouveau container HTML côté tenant.
 * Le tenant ajoute simplement `data-bizzi-mount="audience/<scope>"` sur
 * un container EXISTANT (ex: <aside>, <section>, <div>...). Ce script
 * parse le DOM et y injecte une iframe pointant vers les endpoints
 * /embed/audience/{section,federation,national}/{id} de Bizzi.
 *
 * Usage côté tenant :
 *   <!-- 1) Le backend tenant forge un JWT signé HS256 (clé partagée)  -->
 *   <!-- 2) Il l'expose via meta tag OU data-attribute :               -->
 *   <meta name="bizzi-token" content="eyJhbGc...">
 *   <meta name="bizzi-base"  content="https://bizzi.fr">
 *
 *   <!-- 3) Sur ses containers existants : -->
 *   <aside data-bizzi-mount="audience/section" data-bizzi-id="12">
 *     <!-- contenu fallback SEO/no-JS optionnel -->
 *   </aside>
 *
 *   <!-- 4) Une seule ligne en bas de page : -->
 *   <script src="https://bizzi.fr/embed/audience/loader.js" async></script>
 *
 * Endpoints supportés :
 *   audience/section      → /embed/audience/section/{id}
 *   audience/federation   → /embed/audience/federation/{id}
 *   audience/national     → /embed/audience/national/{id}
 *
 * Conventions data-* (sur le container) :
 *   data-bizzi-mount="<endpoint>"   (REQUIRED)
 *   data-bizzi-id="<int>"           (REQUIRED pour section/federation/national)
 *   data-bizzi-token="<jwt>"        (override du meta global)
 *   data-bizzi-base="<url>"         (override du meta global)
 *   data-bizzi-min-height="<px>"    (initial, default 320)
 *
 * Auto-fit : la page embed envoie `postMessage({type:'bizzi-resize', height})`
 * et le loader ajuste la hauteur de l'iframe.
 *
 * Fallback SEO : si JS désactivé, le contenu enfant du container reste affiché.
 */
(function () {
  "use strict";

  if (window.__bizziLoaderRan) return;
  window.__bizziLoaderRan = true;

  function meta(name) {
    var el = document.querySelector('meta[name="' + name + '"]');
    return el ? el.getAttribute("content") : null;
  }

  var DEFAULT_BASE  = meta("bizzi-base")  || "";
  var DEFAULT_TOKEN = meta("bizzi-token") || "";

  function buildSrc(base, mount, id, token) {
    if (!mount) return null;
    var path = "/embed/" + mount;        // ex: embed/audience/section
    if (id) path += "/" + encodeURIComponent(id);
    var sep = path.indexOf("?") === -1 ? "?" : "&";
    return (base || "") + path + sep + "token=" + encodeURIComponent(token || "");
  }

  function mount(container) {
    if (container.__bizziMounted) return;
    container.__bizziMounted = true;

    var mountKey = container.getAttribute("data-bizzi-mount");
    var id       = container.getAttribute("data-bizzi-id") || "";
    var token    = container.getAttribute("data-bizzi-token") || DEFAULT_TOKEN;
    var base     = container.getAttribute("data-bizzi-base")  || DEFAULT_BASE;
    var minH     = parseInt(container.getAttribute("data-bizzi-min-height") || "320", 10);

    if (!token) {
      console.warn("[bizzi-loader] no token for", mountKey, "— embed skipped");
      return;
    }
    var src = buildSrc(base, mountKey, id, token);
    if (!src) return;

    var iframe = document.createElement("iframe");
    iframe.src = src;
    iframe.title = "Bizzi · " + mountKey;
    iframe.loading = "lazy";
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    iframe.sandbox = "allow-scripts allow-same-origin";
    iframe.style.cssText =
      "width:100%;border:0;display:block;background:transparent;min-height:"
      + minH + "px;height:" + minH + "px;transition:height .2s ease;";

    // Fallback SEO : on garde le contenu original mais on le cache visuellement.
    var fallback = document.createElement("div");
    fallback.style.cssText = "position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden;";
    while (container.firstChild) fallback.appendChild(container.firstChild);
    container.appendChild(fallback);
    container.appendChild(iframe);

    container.__bizziIframe = iframe;
  }

  function mountAll() {
    var nodes = document.querySelectorAll("[data-bizzi-mount]");
    for (var i = 0; i < nodes.length; i++) mount(nodes[i]);
  }

  // Auto-fit via postMessage : message {type:'bizzi-resize', height:N}.
  window.addEventListener("message", function (ev) {
    if (!ev.data || ev.data.type !== "bizzi-resize") return;
    var h = parseInt(ev.data.height, 10);
    if (!h || h < 0) return;
    var iframes = document.querySelectorAll("iframe");
    for (var i = 0; i < iframes.length; i++) {
      var f = iframes[i];
      if (f.contentWindow === ev.source) {
        f.style.height = (h + 4) + "px";
        break;
      }
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountAll);
  } else {
    mountAll();
  }

  // Permet à un SPA tenant de remonter de nouveaux containers à la volée :
  //   window.BizziLoader.scan();
  window.BizziLoader = { scan: mountAll };
})();

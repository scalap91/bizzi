/**
 * bizzi-loader.js — squelette Phase 0
 * ────────────────────────────────────────────────────────────────────────────
 * RÈGLE CRITIQUE PASCAL : ne JAMAIS créer de containers HTML supplémentaires
 * sur les sites tenants. Ce loader scanne le DOM, trouve les containers
 * existants marqués `data-bizzi-mount="..."` et les hydrate via les endpoints
 * /api/org/* et /embed/audience/*.
 *
 * Mount points supportés Phase 0 (org_hierarchy uniquement) :
 *   - data-bizzi-mount="org/territories"   → arbre fédérations + sections
 *   - data-bizzi-mount="org/units-tree"    → liste plate des org_units
 *   - data-bizzi-mount="org/unit-detail"   → fiche d'un unit (+ data-bizzi-id)
 *
 * Future Phase 1 (audience, broadcasts) :
 *   - data-bizzi-mount="audience/section"
 *   - data-bizzi-mount="audience/federation"
 *   - data-bizzi-mount="audience/national"
 *   - data-bizzi-mount="org/broadcasts/received"
 *
 * Configuration côté tenant (avant inclusion du loader) :
 *   <script>
 *     window.BizziConfig = {
 *       apiBase: "https://bizzi.fr",      // racine API Bizzi
 *       tenantId: 4,                       // id tenant
 *       jwt: "<JWT signé par backend tenant>",
 *     };
 *   </script>
 *   <script src="https://bizzi.fr/bizzi-loader.js" defer></script>
 *
 * Aucune modification du DOM des containers existants en dehors de leur
 * contenu interne. Pas de styles inline imposés.
 */
(function () {
  "use strict";

  const DEFAULTS = { apiBase: "https://bizzi.fr", tenantId: null, jwt: null };
  const cfg = Object.assign({}, DEFAULTS, window.BizziConfig || {});

  function authHeaders() {
    const h = { "Accept": "application/json" };
    if (cfg.jwt) h["Authorization"] = "Bearer " + cfg.jwt;
    return h;
  }

  async function fetchJson(path) {
    const r = await fetch(cfg.apiBase + path, { headers: authHeaders(), credentials: "omit" });
    if (!r.ok) throw new Error("Bizzi " + path + " " + r.status);
    return r.json();
  }

  // ─── Renderers (DOM minimal, le tenant style via CSS) ─────────────────────

  function renderUnitsTree(roots, container) {
    const ul = document.createElement("ul");
    ul.className = "bizzi-org-tree";
    function walk(node, parent) {
      const li = document.createElement("li");
      li.dataset.bizziUnitId = node.id;
      li.dataset.bizziLevel = node.level;
      const span = document.createElement("span");
      span.className = "bizzi-org-unit";
      span.textContent = node.name + " (" + node.level + ")";
      li.appendChild(span);
      if (node.children && node.children.length) {
        const sub = document.createElement("ul");
        node.children.forEach(c => walk(c, sub));
        li.appendChild(sub);
      }
      parent.appendChild(li);
    }
    roots.forEach(r => walk(r, ul));
    container.replaceChildren(ul);
  }

  function renderUnitsFlat(units, container) {
    const ul = document.createElement("ul");
    ul.className = "bizzi-org-list";
    units.forEach(u => {
      const li = document.createElement("li");
      li.dataset.bizziUnitId = u.id;
      li.dataset.bizziLevel = u.level;
      li.textContent = "[" + u.level + "] " + u.name;
      ul.appendChild(li);
    });
    container.replaceChildren(ul);
  }

  function renderUnitDetail(unit, container) {
    const div = document.createElement("div");
    div.className = "bizzi-org-unit-detail";
    div.dataset.bizziUnitId = unit.id;
    const fields = [
      ["Nom", unit.name],
      ["Niveau", unit.level],
      ["Responsable", unit.responsible || "—"],
      ["Email", unit.contact_email || "—"],
    ];
    fields.forEach(([k, v]) => {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      div.appendChild(dt); div.appendChild(dd);
    });
    container.replaceChildren(div);
  }

  // ─── Hydrators par mount type ─────────────────────────────────────────────

  const hydrators = {
    "org/territories": async (el) => {
      const data = await fetchJson("/api/org/units/tree?tenant_id=" + cfg.tenantId);
      renderUnitsTree(data.roots || [], el);
    },
    "org/units-tree": async (el) => {
      const data = await fetchJson("/api/org/units?tenant_id=" + cfg.tenantId);
      renderUnitsFlat(data.units || [], el);
    },
    "org/unit-detail": async (el) => {
      const id = el.dataset.bizziId;
      if (!id) throw new Error("org/unit-detail nécessite data-bizzi-id");
      const unit = await fetchJson("/api/org/units/" + encodeURIComponent(id));
      renderUnitDetail(unit, el);
    },
  };

  // ─── Scan + hydrate ───────────────────────────────────────────────────────

  async function hydrateAll() {
    if (!cfg.tenantId) {
      console.warn("[bizzi-loader] window.BizziConfig.tenantId manquant — abort");
      return;
    }
    const els = document.querySelectorAll("[data-bizzi-mount]");
    for (const el of els) {
      const mount = el.dataset.bizziMount;
      const hydrator = hydrators[mount];
      if (!hydrator) continue;  // Phase 1 mount points ignorés ici
      try {
        el.dataset.bizziLoading = "1";
        await hydrator(el);
        delete el.dataset.bizziLoading;
        el.dataset.bizziLoaded = "1";
      } catch (e) {
        delete el.dataset.bizziLoading;
        el.dataset.bizziError = String(e.message || e);
        console.error("[bizzi-loader]", mount, e);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hydrateAll, { once: true });
  } else {
    hydrateAll();
  }

  window.Bizzi = Object.assign(window.Bizzi || {}, {
    config: cfg,
    hydrate: hydrateAll,
    registerHydrator: (name, fn) => { hydrators[name] = fn; },
  });
})();

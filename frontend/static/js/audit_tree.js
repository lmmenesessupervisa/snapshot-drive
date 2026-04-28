// /audit — vista "Por proyecto": árbol jerárquico construido sobre
// /audit/api/tree (rclone lsjson sobre la raíz del Drive). Cargado con
// `defer`, así que apiFetch (auth.js) ya está inicializado.

const $T = (id) => document.getElementById(id);

const TREE_STATE = {
  data: null,
  filter: "",
  withDb: false,
  encryptedOnly: false,
  staleOnly: false,
  expanded: new Set(),  // keys: `p:<proyecto>` o `r:<proyecto>/<entorno>/<pais>` o `c:<proyecto>/<entorno>/<pais>/<label>`
  view: "tree",
};

// ---------- formatters ----------
function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, x = Number(n);
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function fmtTsShort(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short", hour12: false });
  } catch { return iso; }
}

function ageDays(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return (Date.now() - d.getTime()) / 86400000;
}

function ageChip(iso) {
  const days = ageDays(iso);
  if (days == null) return '<span class="chip chip-slate">sin fecha</span>';
  if (days < 1) return `<span class="chip chip-emerald">${Math.round(days * 24)}h</span>`;
  if (days < 7) return `<span class="chip chip-emerald">${days.toFixed(1)}d</span>`;
  if (days < 14) return `<span class="chip chip-amber">${days.toFixed(0)}d</span>`;
  return `<span class="chip chip-rose">${days.toFixed(0)}d</span>`;
}

function cryptoChip(c) {
  return ({
    age: '<span class="chip chip-emerald">age</span>',
    openssl: '<span class="chip chip-amber">openssl</span>',
    none: '<span class="chip chip-slate">sin cifrar</span>',
  })[c] || `<span class="chip chip-slate">${c}</span>`;
}

function engineChip(engine) {
  return ({
    linux: '<span class="chip chip-violet">os/linux</span>',
    postgres: '<span class="chip chip-sky">postgres</span>',
    mysql: '<span class="chip chip-amber">mysql</span>',
    mongo: '<span class="chip chip-emerald">mongo</span>',
  })[engine] || `<span class="chip chip-slate">${engine}</span>`;
}

// ---------- filters ----------
function passesFilter(p) {
  const needle = TREE_STATE.filter.toLowerCase();
  if (!needle && !TREE_STATE.withDb && !TREE_STATE.encryptedOnly && !TREE_STATE.staleOnly) return p;
  const filtered = {
    ...p,
    regions: p.regions
      .map(r => ({
        ...r,
        clients: r.clients.filter(c => {
          if (TREE_STATE.withDb && (!c.db || c.db.length === 0)) return false;
          if (TREE_STATE.encryptedOnly) {
            const hasEnc = (c.monthly && c.monthly.encrypted_count > 0) ||
                           (c.db || []).some(d => d.encrypted_count > 0);
            if (!hasEnc) return false;
          }
          if (TREE_STATE.staleOnly) {
            const days = ageDays(c.last_ts);
            if (days == null || days < 7) return false;
          }
          if (needle) {
            const hay =
              p.name.toLowerCase().includes(needle) ||
              c.label.toLowerCase().includes(needle) ||
              (c.db || []).some(d => `${d.subkey}`.toLowerCase().includes(needle));
            if (!hay) return false;
          }
          return true;
        }),
      }))
      .filter(r => r.clients.length > 0),
  };
  return filtered.regions.length ? filtered : null;
}

// ---------- render ----------
function renderTreeKpis() {
  const s = TREE_STATE.data?.summary || {};
  $T("kt-proyectos").textContent = s.proyectos ?? "0";
  $T("kt-clients").textContent   = s.clients ?? "0";
  $T("kt-files").textContent     = s.files ?? "0";
  $T("kt-size").textContent      = fmtBytes(s.size_bytes);
  $T("kt-last").textContent      = fmtTsShort(s.last_backup_ts);
  if (s.scanned_at) {
    $T("audit-last-ts").textContent = new Date(s.scanned_at * 1000).toLocaleTimeString("es-ES", { hour12: false });
  }
}

function renderClient(p, r, c) {
  const key = `c:${p.name}/${r.entorno}/${r.pais}/${c.label}`;
  const open = TREE_STATE.expanded.has(key);
  const dbCount = (c.db || []).length;

  const monthlyChip = c.monthly
    ? `<span class="chip chip-violet">mensual · ${c.monthly.count}</span>`
    : `<span class="chip chip-slate">sin mensual</span>`;
  const dbChip = dbCount > 0
    ? `<span class="chip chip-sky">${dbCount} DB${dbCount > 1 ? 's' : ''}</span>`
    : '';

  const drilldown = open ? renderClientDetail(c) : '';

  return `
    <div class="audit-cli">
      <button class="audit-cli-row" data-toggle="${key}">
        <span class="audit-caret">${open ? '▾' : '▸'}</span>
        <span class="audit-cli-label mono">${c.label}</span>
        <span class="audit-cli-chips">${monthlyChip} ${dbChip}</span>
        <span class="audit-cli-meta">${c.files} archivos · ${fmtBytes(c.size)}</span>
        <span class="audit-cli-age">${ageChip(c.last_ts)}</span>
      </button>
      ${drilldown}
    </div>
  `;
}

function renderClientDetail(c) {
  const blocks = [];
  if (c.monthly) blocks.push(renderBackupBlock("Backup mensual del sistema", c.monthly));
  for (const d of c.db || []) {
    blocks.push(renderBackupBlock(`Backup DB · ${d.subkey}`, d));
  }
  if (!blocks.length) {
    return `<div class="audit-cli-detail"><div class="text-xs text-[var(--muted)] py-3">Sin backups detectados.</div></div>`;
  }
  return `<div class="audit-cli-detail">${blocks.join("")}</div>`;
}

function renderBackupBlock(title, b) {
  const recent = (b.recent || []).map(f => `
    <tr>
      <td class="mono text-xs py-1.5">${fmtTsShort(f.ts_iso)}</td>
      <td class="mono text-xs py-1.5">${fmtBytes(f.size)}</td>
      <td class="py-1.5">${cryptoChip(f.crypto)}</td>
      <td class="mono text-[11px] text-[var(--muted-2)] py-1.5 break-all">${f.path}</td>
    </tr>
  `).join("");
  return `
    <div class="audit-bk-block">
      <div class="audit-bk-header">
        <div class="flex items-center gap-2">
          ${engineChip(b.engine)}
          <span class="font-semibold text-sm">${title}</span>
        </div>
        <div class="flex items-center gap-3 text-xs text-[var(--muted)]">
          <span>${b.count} archivos</span>
          <span>${fmtBytes(b.size)}</span>
          ${b.encrypted_count > 0 ? `<span class="chip chip-emerald">${b.encrypted_count} cifrados</span>` : ''}
          <span>último: <b class="mono">${fmtTsShort(b.newest_ts)}</b></span>
        </div>
      </div>
      <table class="w-full audit-bk-table">
        <thead>
          <tr>
            <th class="text-left">Fecha</th>
            <th class="text-left">Tamaño</th>
            <th class="text-left">Cifrado</th>
            <th class="text-left">Path en Drive</th>
          </tr>
        </thead>
        <tbody>${recent || `<tr><td colspan="4" class="text-center text-xs text-[var(--muted)] py-2">sin archivos</td></tr>`}</tbody>
      </table>
    </div>
  `;
}

function renderRegion(p, r) {
  const key = `r:${p.name}/${r.entorno}/${r.pais}`;
  const open = TREE_STATE.expanded.has(key);
  const clients = open
    ? r.clients.map(c => renderClient(p, r, c)).join("")
    : "";
  return `
    <div class="audit-region">
      <button class="audit-region-row" data-toggle="${key}">
        <span class="audit-caret">${open ? '▾' : '▸'}</span>
        <span class="chip chip-slate">${r.entorno}</span>
        <span class="chip chip-slate">${r.pais}</span>
        <span class="audit-region-meta">${r.clients.length} cliente${r.clients.length === 1 ? '' : 's'} · ${r.files} archivos · ${fmtBytes(r.size)}</span>
      </button>
      ${open ? `<div class="audit-region-body">${clients}</div>` : ''}
    </div>
  `;
}

function renderProyecto(p) {
  const key = `p:${p.name}`;
  const open = TREE_STATE.expanded.has(key);
  return `
    <div class="card audit-proyecto">
      <button class="audit-proyecto-row" data-toggle="${key}">
        <div class="flex items-center gap-3">
          <span class="audit-caret-lg">${open ? '▾' : '▸'}</span>
          <span class="audit-proyecto-name">${p.name}</span>
          <span class="chip chip-violet">${p.clients} cliente${p.clients === 1 ? '' : 's'}</span>
        </div>
        <div class="flex items-center gap-4 text-sm text-[var(--muted)]">
          <span>${p.files} archivos</span>
          <span class="mono">${fmtBytes(p.size)}</span>
          <span>último: ${ageChip(p.last_ts)}</span>
        </div>
      </button>
      ${open ? `<div class="audit-proyecto-body">${p.regions.map(r => renderRegion(p, r)).join("")}</div>` : ''}
    </div>
  `;
}

function renderTree() {
  if (!TREE_STATE.data) return;
  renderTreeKpis();
  const root = $T("tree-root");
  const proyectos = (TREE_STATE.data.proyectos || [])
    .map(passesFilter)
    .filter(Boolean);
  if (!proyectos.length) {
    root.innerHTML = `<div class="card p-8 text-center text-[var(--muted)]">Sin resultados con los filtros activos.</div>`;
    return;
  }
  root.innerHTML = proyectos.map(renderProyecto).join("");

  root.querySelectorAll("[data-toggle]").forEach(b => {
    b.addEventListener("click", () => {
      const k = b.dataset.toggle;
      if (TREE_STATE.expanded.has(k)) TREE_STATE.expanded.delete(k);
      else TREE_STATE.expanded.add(k);
      renderTree();
    });
  });
}

async function loadTree(force = false) {
  try {
    const url = "/audit/api/tree" + (force ? "?force=1" : "");
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/auth/login"; return; }
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || "error desconocido");
    TREE_STATE.data = j;
    document.getElementById("audit-error").classList.add("hidden");
    renderTree();
  } catch (e) {
    const el = document.getElementById("audit-error");
    el.textContent = "No se pudo cargar el árbol: " + e.message;
    el.classList.remove("hidden");
  }
}

// ---------- tabs ----------
function showView(view) {
  TREE_STATE.view = view;
  $T("view-tree").classList.toggle("hidden", view !== "tree");
  $T("view-host").classList.toggle("hidden", view !== "host");
  $T("tab-tree").classList.toggle("audit-tab-active", view === "tree");
  $T("tab-host").classList.toggle("audit-tab-active", view === "host");
  if (view === "tree" && !TREE_STATE.data) loadTree(false);
}

document.addEventListener("DOMContentLoaded", () => {
  $T("tab-tree").addEventListener("click", () => showView("tree"));
  $T("tab-host").addEventListener("click", () => showView("host"));
  $T("filter-tree").addEventListener("input", (e) => {
    TREE_STATE.filter = e.target.value.trim();
    renderTree();
  });
  $T("filter-with-db").addEventListener("change", (e) => { TREE_STATE.withDb = e.target.checked; renderTree(); });
  $T("filter-encrypted").addEventListener("change", (e) => { TREE_STATE.encryptedOnly = e.target.checked; renderTree(); });
  $T("filter-stale").addEventListener("change", (e) => { TREE_STATE.staleOnly = e.target.checked; renderTree(); });

  // Hook al botón Refrescar para que también refresque el árbol.
  const refreshBtn = $T("btn-refresh");
  refreshBtn.addEventListener("click", () => {
    if (TREE_STATE.view === "tree") loadTree(true);
  });

  // Carga inicial del árbol (vista por defecto)
  loadTree(false);
});

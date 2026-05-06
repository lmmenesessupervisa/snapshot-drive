// /audit — vista "Por proyecto": árbol jerárquico construido sobre
// /audit/api/tree (rclone lsjson sobre la raíz del Drive). Cargado con
// `defer`, así que apiFetch (auth.js) ya está inicializado.

const $T = (id) => document.getElementById(id);

const TREE_STATE = {
  data: null,
  filter: "",
  filterProyecto: "",
  filterCliente: "",
  withDb: false,
  encryptedOnly: false,
  staleOnly: false,
  shrunkOnly: false,
  expanded: new Set(),  // keys: `p:<proyecto>` o `r:<proyecto>/<entorno>/<pais>` o `c:<proyecto>/<entorno>/<pais>/<label>`
  view: "tree",
};

// SVG chevron — gira con CSS via la clase rotated.
const CHEVRON_SVG = `<svg class="audit-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>`;

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

// Pill de estado del SERVICIO snapctl en el cliente — viene de la lectura
// de _status/<host>.json. "running" = un backup está en curso ahora; "ok" =
// reportó OK reciente; "fail"/"silent"/"unreported" = atención requerida.
function serviceChip(svc) {
  if (!svc || !svc.health) {
    return '<span class="chip chip-slate" title="sin datos de servicio">servicio: ?</span>';
  }
  const cfg = ({
    ok:         { cls: "chip-emerald", label: "servicio OK",      title: "snapctl reportó OK reciente" },
    running:    { cls: "chip-sky",     label: "operación en curso", title: "snapctl está corriendo un backup ahora" },
    fail:       { cls: "chip-rose",    label: "servicio FALLO",   title: "última operación falló" },
    silent:     { cls: "chip-amber",   label: "servicio silencioso", title: "no hay backup OK reciente" },
    unreported: { cls: "chip-violet",  label: "sin reportar",     title: "no hay _status/<host>.json — el cliente no está reportando" },
    unknown:    { cls: "chip-slate",   label: "estado: ?",        title: "estado desconocido" },
  })[svc.health] || { cls: "chip-slate", label: svc.health, title: "" };
  return `<span class="chip ${cfg.cls}" title="${cfg.title}">${cfg.label}</span>`;
}

function shrinkChip(b) {
  if (!b || !b.shrunk) return '';
  const pct = (b.shrink_delta_pct != null) ? b.shrink_delta_pct.toFixed(1) : "?";
  const prev = (b.prev_size != null) ? fmtBytes(b.prev_size) : "?";
  const curr = (b.newest_size != null) ? fmtBytes(b.newest_size) : "?";
  return `<span class="chip chip-amber" title="último: ${curr} · anterior: ${prev}">⚠ ${pct}% menor</span>`;
}

function clientHasShrunk(c) {
  if (c.shrunk && c.shrunk > 0) return true;
  if (c.monthly && c.monthly.shrunk) return true;
  return (c.db || []).some(d => d.shrunk);
}

// ---------- filters ----------
function passesFilter(p) {
  // Filtro por proyecto exacto del dropdown
  if (TREE_STATE.filterProyecto && p.name !== TREE_STATE.filterProyecto) return null;

  const needle = TREE_STATE.filter.toLowerCase();
  const noFilter = !needle && !TREE_STATE.withDb && !TREE_STATE.encryptedOnly &&
                   !TREE_STATE.staleOnly && !TREE_STATE.shrunkOnly;
  if (noFilter) return p;
  const filtered = {
    ...p,
    regions: p.regions
      .map(r => ({
        ...r,
        clients: r.clients.filter(c => {
          if (cli && c.label !== cli) return false;
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
          if (TREE_STATE.shrunkOnly && !clientHasShrunk(c)) return false;
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

// Pobla los dropdowns proyecto / cliente con valores únicos del data.
function populateFilterDropdowns() {
  if (!TREE_STATE.data) return;
  const projSel = $T("filter-tree-proyecto");
  const cliSel  = $T("filter-tree-cliente");
  if (!projSel || !cliSel) return;
  const proyectos = (TREE_STATE.data.proyectos || []).map(p => p.name).sort();
  const clientes = new Set();
  for (const p of TREE_STATE.data.proyectos || []) {
    for (const r of p.regions || []) for (const c of r.clients || []) clientes.add(c.label);
  }
  const cliSorted = [...clientes].sort();
  // Conservar la selección actual al re-popular.
  const curP = projSel.value, curC = cliSel.value;
  projSel.innerHTML = '<option value="">Todos los proyectos</option>' +
    proyectos.map(n => `<option value="${n}"${n === curP ? ' selected' : ''}>${n}</option>`).join("");
  cliSel.innerHTML = '<option value="">Todos los clientes</option>' +
    cliSorted.map(n => `<option value="${n}"${n === curC ? ' selected' : ''}>${n}</option>`).join("");
}

// ---------- render ----------
function countAliveServices() {
  // Recorre el árbol y cuenta clientes con health ∈ {ok, running}.
  let total = 0, alive = 0;
  for (const p of TREE_STATE.data?.proyectos || []) {
    for (const r of p.regions || []) {
      for (const c of r.clients || []) {
        total++;
        const h = c.service && c.service.health;
        if (h === "ok" || h === "running") alive++;
      }
    }
  }
  return { alive, total };
}

function renderTreeKpis() {
  const s = TREE_STATE.data?.summary || {};
  $T("kt-proyectos").textContent = s.proyectos ?? "0";
  $T("kt-clients").textContent   = s.clients ?? "0";
  $T("kt-files").textContent     = s.files ?? "0";
  $T("kt-size").textContent      = fmtBytes(s.size_bytes);
  $T("kt-last").textContent      = fmtTsShort(s.last_backup_ts);

  // Servicios vivos
  const live = countAliveServices();
  $T("kt-alive").textContent = `${live.alive}`;
  $T("kt-alive-sub").textContent = `de ${live.total} cliente${live.total === 1 ? '' : 's'}`;
  $T("kt-alive").className = "text-3xl font-semibold mt-2 " +
    (live.total === 0 ? "text-[var(--muted)]"
      : live.alive === live.total ? "text-emerald-300"
      : live.alive === 0 ? "text-rose-300" : "text-amber-300");

  // Encogidos + banner
  const shrunk = s.shrunk ?? 0;
  $T("kt-shrunk").textContent = shrunk;
  $T("kt-shrunk").className = "text-3xl font-semibold mt-2 " +
    (shrunk > 0 ? "text-amber-300" : "text-[var(--muted)]");
  $T("kt-shrink-pct").textContent = s.shrink_pct_threshold ?? 20;
  const banner = $T("shrink-banner");
  if (shrunk > 0) {
    $T("shrink-banner-msg").textContent =
      `${shrunk} backup${shrunk === 1 ? '' : 's'} más reciente${shrunk === 1 ? '' : 's'} pesa${shrunk === 1 ? '' : 'n'} al menos ${s.shrink_pct_threshold ?? 20}% menos que el anterior. Verifica que no esté faltando data.`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }

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
  const svcChip = serviceChip(c.service);
  const shrCount = c.shrunk || 0;
  const shrunkBadge = shrCount > 0
    ? `<span class="chip chip-amber" title="${shrCount} backup(s) pesa(n) menos que el anterior">⚠ ${shrCount} encogido${shrCount === 1 ? '' : 's'}</span>`
    : '';

  const drilldown = open ? renderClientDetail(c) : '';

  return `
    <div class="audit-cli">
      <button class="audit-cli-row" data-toggle="${key}">
        <span class="audit-caret">${open ? CHEVRON_SVG.replace('audit-chevron', 'audit-chevron rotated') : CHEVRON_SVG}</span>
        <span class="audit-cli-label mono">${c.label}</span>
        <span class="audit-cli-chips">${svcChip} ${monthlyChip} ${dbChip} ${shrunkBadge}</span>
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
  // Marca con un dot ámbar la fila más reciente cuando está encogida.
  const recent = (b.recent || []).map((f, i) => {
    const isNewest = i === 0;
    const shrinkDot = isNewest && b.shrunk
      ? '<span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5" title="encogido vs anterior"></span>'
      : '';
    return `
      <tr ${isNewest && b.shrunk ? 'class="bg-amber-500/5"' : ''}>
        <td class="mono text-xs py-1.5">${shrinkDot}${fmtTsShort(f.ts_iso)}</td>
        <td class="mono text-xs py-1.5">${fmtBytes(f.size)}</td>
        <td class="py-1.5">${cryptoChip(f.crypto)}</td>
        <td class="mono text-[11px] text-[var(--muted-2)] py-1.5 break-all">${f.path}</td>
      </tr>
    `;
  }).join("");
  return `
    <div class="audit-bk-block ${b.shrunk ? 'border-amber-500/30' : ''}">
      <div class="audit-bk-header">
        <div class="flex items-center gap-2">
          ${engineChip(b.engine)}
          <span class="font-semibold text-sm">${title}</span>
          ${shrinkChip(b)}
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
  const shrunkBadge = (r.shrunk || 0) > 0
    ? `<span class="chip chip-amber">⚠ ${r.shrunk} encogido${r.shrunk === 1 ? '' : 's'}</span>`
    : '';
  return `
    <div class="audit-region">
      <button class="audit-region-row" data-toggle="${key}">
        <span class="audit-caret">${open ? CHEVRON_SVG.replace('audit-chevron', 'audit-chevron rotated') : CHEVRON_SVG}</span>
        <span class="chip chip-slate">${r.entorno}</span>
        <span class="chip chip-slate">${r.pais}</span>
        ${shrunkBadge}
        <span class="audit-region-meta">${r.clients.length} cliente${r.clients.length === 1 ? '' : 's'} · ${r.files} archivos · ${fmtBytes(r.size)}</span>
      </button>
      ${open ? `<div class="audit-region-body">${clients}</div>` : ''}
    </div>
  `;
}

function renderProyecto(p) {
  const key = `p:${p.name}`;
  const open = TREE_STATE.expanded.has(key);
  const shrunkBadge = (p.shrunk || 0) > 0
    ? `<span class="chip chip-amber">⚠ ${p.shrunk} encogido${p.shrunk === 1 ? '' : 's'}</span>`
    : '';
  return `
    <div class="card audit-proyecto">
      <button class="audit-proyecto-row" data-toggle="${key}">
        <div class="flex items-center gap-3">
          <span class="audit-caret-lg">${open ? CHEVRON_SVG.replace('audit-chevron', 'audit-chevron rotated') : CHEVRON_SVG}</span>
          <span class="audit-proyecto-name">${p.name}</span>
          <span class="chip chip-violet">${p.clients} cliente${p.clients === 1 ? '' : 's'}</span>
          ${shrunkBadge}
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
  const allProyectos = TREE_STATE.data.proyectos || [];
  const proyectos = allProyectos.map(passesFilter).filter(Boolean);
  if (!proyectos.length) {
    // Distinguir "no hay datos del todo" de "los filtros excluyen todo".
    if (!allProyectos.length) {
      root.innerHTML = `
        <div class="card p-8 text-center space-y-3">
          <div class="text-base font-semibold">Aún no hay backups indexados</div>
          <div class="text-sm text-[var(--muted)]">
            La base de datos del inventario está vacía. Si ya vinculaste
            Drive y tus clientes están subiendo archivos, da clic a
            <span class="font-medium">Refrescar</span> para escanear ahora.
          </div>
          <div class="text-xs text-[var(--muted-2)]">
            Solo se cuentan archivos cuya ruta sigue la taxonomía
            <code class="mono">proyecto/entorno/pais/{os|db}/&lt;tipo&gt;/&lt;label&gt;/AAAA/MM/DD/&lt;archivo&gt;</code>.
          </div>
        </div>`;
    } else {
      root.innerHTML = `<div class="card p-8 text-center text-[var(--muted)]">Sin resultados con los filtros activos.</div>`;
    }
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
    populateFilterDropdowns();
    renderTree();
    // Notificar a audit_summary.js que hay data fresca.
    if (typeof window.onAuditTreeData === "function") {
      window.onAuditTreeData(j);
    }
  } catch (e) {
    const el = document.getElementById("audit-error");
    el.textContent = "No se pudo cargar el árbol: " + e.message;
    el.classList.remove("hidden");
  }
}

// ============================================================
// SPOTLIGHT — paneles drilldown que abren las KPI cards.
// Cada modo agrega los datos del árbol según la dimensión y los renderiza
// en una tabla sortable. Botón "Volver al árbol" cierra y restaura.
// ============================================================

const SPOTLIGHT = {
  open: false,
  kind: null,            // proyectos|clientes|vivos|archivos|tamano|ultimo|encogidos
  sortKey: null,
  sortAsc: false,
};

function flatClients() {
  const out = [];
  for (const p of TREE_STATE.data?.proyectos || []) {
    for (const r of p.regions || []) {
      for (const c of r.clients || []) {
        out.push({
          ...c,
          proyecto: p.name,
          entorno: r.entorno,
          pais: r.pais,
        });
      }
    }
  }
  return out;
}

function spotlightSortable(headers, rows) {
  // headers: [{key, label, align?: 'right'|'left', fmt?}]
  // rows: lista plana de objetos
  const k = SPOTLIGHT.sortKey || headers[0].key;
  const dir = SPOTLIGHT.sortAsc ? 1 : -1;
  rows = [...rows].sort((a, b) => {
    let va = a[k], vb = b[k];
    if (va == null) va = "";
    if (vb == null) vb = "";
    if (typeof va === "string" && typeof vb === "string") {
      va = va.toLowerCase(); vb = vb.toLowerCase();
    }
    if (va < vb) return -1 * dir;
    if (va > vb) return  1 * dir;
    return 0;
  });

  const ths = headers.map(h => `
    <th class="audit-sortable text-${h.align || 'left'}" data-spot-sort="${h.key}">
      ${h.label}
      <span class="audit-sort-arrow">${SPOTLIGHT.sortKey === h.key ? (SPOTLIGHT.sortAsc ? '▲' : '▼') : '↕'}</span>
    </th>
  `).join("");

  const trs = rows.map(row => {
    return `<tr class="border-t border-[var(--border)]">${
      headers.map(h => {
        const v = h.fmt ? h.fmt(row[h.key], row) : (row[h.key] ?? "—");
        return `<td class="px-4 py-2 text-${h.align || 'left'}">${v}</td>`;
      }).join("")
    }</tr>`;
  }).join("");

  return `
    <div class="overflow-hidden rounded border border-[var(--border)]">
      <table class="w-full text-sm">
        <thead class="bg-[var(--surface)]"><tr>${ths}</tr></thead>
        <tbody>${trs || `<tr><td class="py-8 text-center text-[var(--muted)]" colspan="${headers.length}">Sin datos.</td></tr>`}</tbody>
      </table>
    </div>
  `;
}

function spotlightProyectos() {
  const rows = (TREE_STATE.data?.proyectos || []).map(p => ({
    name: p.name,
    clients: p.clients,
    files: p.files,
    size: p.size,
    shrunk: p.shrunk || 0,
    last_ts: p.last_ts || "",
  }));
  return {
    title: `Proyectos (${rows.length})`,
    body: spotlightSortable([
      { key: "name", label: "Proyecto" },
      { key: "clients", label: "Clientes", align: "right" },
      { key: "files", label: "Archivos", align: "right" },
      { key: "size", label: "Tamaño", align: "right",
        fmt: v => `<span class="mono">${fmtBytes(v)}</span>` },
      { key: "shrunk", label: "Encogidos", align: "right",
        fmt: v => v > 0 ? `<span class="chip chip-amber">⚠ ${v}</span>` : '<span class="text-[var(--muted-2)]">0</span>' },
      { key: "last_ts", label: "Último backup", align: "right",
        fmt: v => `<span class="mono">${fmtTsShort(v)}</span> ${ageChip(v)}` },
    ], rows),
  };
}

function spotlightClientes(filterFn = null) {
  let rows = flatClients().map(c => ({
    label: c.label,
    proyecto: c.proyecto,
    region: `${c.entorno}/${c.pais}`,
    files: c.files,
    size: c.size,
    shrunk: c.shrunk || 0,
    service: c.service?.health || "unknown",
    last_ts: c.last_ts || "",
  }));
  if (filterFn) rows = rows.filter(filterFn);
  const title = filterFn ? `Clientes filtrados (${rows.length})` : `Clientes (${rows.length})`;
  return {
    title,
    body: spotlightSortable([
      { key: "label", label: "Cliente",
        fmt: v => `<span class="mono">${v}</span>` },
      { key: "proyecto", label: "Proyecto" },
      { key: "region", label: "Región" },
      { key: "service", label: "Servicio",
        fmt: (v, row) => serviceChip({ health: v }) },
      { key: "files", label: "Archivos", align: "right" },
      { key: "size", label: "Tamaño", align: "right",
        fmt: v => `<span class="mono">${fmtBytes(v)}</span>` },
      { key: "shrunk", label: "Encogidos", align: "right",
        fmt: v => v > 0 ? `<span class="chip chip-amber">⚠ ${v}</span>` : '<span class="text-[var(--muted-2)]">0</span>' },
      { key: "last_ts", label: "Último", align: "right",
        fmt: v => ageChip(v) },
    ], rows),
  };
}

function spotlightVivos() {
  const aliveSet = new Set(["ok", "running"]);
  return {
    ...spotlightClientes(c => aliveSet.has(c.service)),
    title: undefined,  // sobreescribiremos
    _customTitle: `Servicios vivos (ok / en curso)`,
  };
}

function spotlightArchivos() {
  // Breakdown por tipo: sistema vs DB engines.
  const buckets = { "os/linux": { files: 0, size: 0, shrunk: 0, clients: new Set() } };
  for (const c of flatClients()) {
    if (c.monthly) {
      buckets["os/linux"].files += c.monthly.count;
      buckets["os/linux"].size += c.monthly.size;
      if (c.monthly.shrunk) buckets["os/linux"].shrunk++;
      buckets["os/linux"].clients.add(c.label);
    }
    for (const d of c.db || []) {
      const k = `db/${d.subkey}`;
      if (!buckets[k]) buckets[k] = { files: 0, size: 0, shrunk: 0, clients: new Set() };
      buckets[k].files += d.count;
      buckets[k].size += d.size;
      if (d.shrunk) buckets[k].shrunk++;
      buckets[k].clients.add(c.label);
    }
  }
  const rows = Object.entries(buckets).map(([key, b]) => ({
    tipo: key,
    clientes: b.clients.size,
    archivos: b.files,
    size: b.size,
    shrunk: b.shrunk,
  }));
  return {
    title: `Archivos por tipo de backup`,
    body: spotlightSortable([
      { key: "tipo", label: "Tipo",
        fmt: v => engineChip(v.startsWith("db/") ? v.slice(3) : v.split("/")[1] || v) },
      { key: "clientes", label: "Clientes", align: "right" },
      { key: "archivos", label: "Archivos", align: "right" },
      { key: "size", label: "Tamaño", align: "right",
        fmt: v => `<span class="mono">${fmtBytes(v)}</span>` },
      { key: "shrunk", label: "Encogidos", align: "right",
        fmt: v => v > 0 ? `<span class="chip chip-amber">⚠ ${v}</span>` : '<span class="text-[var(--muted-2)]">0</span>' },
    ], rows),
  };
}

function spotlightTamano() {
  // Top clientes por tamaño total.
  const rows = flatClients().map(c => ({
    label: c.label,
    proyecto: c.proyecto,
    region: `${c.entorno}/${c.pais}`,
    files: c.files,
    size: c.size,
    last_ts: c.last_ts || "",
  })).sort((a, b) => b.size - a.size);
  // Sin override de SPOTLIGHT.sortKey para que el default sea "size desc".
  if (!SPOTLIGHT.sortKey) { SPOTLIGHT.sortKey = "size"; SPOTLIGHT.sortAsc = false; }
  return {
    title: `Top clientes por tamaño`,
    body: spotlightSortable([
      { key: "label", label: "Cliente",
        fmt: v => `<span class="mono">${v}</span>` },
      { key: "proyecto", label: "Proyecto" },
      { key: "region", label: "Región" },
      { key: "size", label: "Tamaño", align: "right",
        fmt: v => `<span class="mono">${fmtBytes(v)}</span>` },
      { key: "files", label: "Archivos", align: "right" },
      { key: "last_ts", label: "Último", align: "right",
        fmt: v => ageChip(v) },
    ], rows),
  };
}

function spotlightUltimo() {
  const rows = flatClients()
    .filter(c => c.last_ts)
    .map(c => ({
      label: c.label,
      proyecto: c.proyecto,
      region: `${c.entorno}/${c.pais}`,
      last_ts: c.last_ts,
      service: c.service?.health || "unknown",
    }))
    .sort((a, b) => (b.last_ts > a.last_ts ? 1 : -1));
  if (!SPOTLIGHT.sortKey) { SPOTLIGHT.sortKey = "last_ts"; SPOTLIGHT.sortAsc = false; }
  return {
    title: `Ranking por recencia del último backup`,
    body: spotlightSortable([
      { key: "label", label: "Cliente",
        fmt: v => `<span class="mono">${v}</span>` },
      { key: "proyecto", label: "Proyecto" },
      { key: "region", label: "Región" },
      { key: "last_ts", label: "Última fecha",
        fmt: v => `<span class="mono">${fmtTsShort(v)}</span>` },
      { key: "last_ts", label: "Edad", align: "right",
        fmt: v => ageChip(v) },
      { key: "service", label: "Servicio",
        fmt: v => serviceChip({ health: v }) },
    ], rows),
  };
}

function spotlightEncogidos() {
  // Lista todas las leaves shrunk.
  const rows = [];
  for (const c of flatClients()) {
    const blocks = [];
    if (c.monthly && c.monthly.shrunk) blocks.push(c.monthly);
    for (const d of c.db || []) if (d.shrunk) blocks.push(d);
    for (const b of blocks) {
      rows.push({
        label: c.label,
        proyecto: c.proyecto,
        region: `${c.entorno}/${c.pais}`,
        engine: b.engine,
        prev_size: b.prev_size,
        newest_size: b.newest_size,
        delta_pct: b.shrink_delta_pct,
        newest_ts: b.newest_ts,
      });
    }
  }
  return {
    title: `Backups encogidos (${rows.length})`,
    body: spotlightSortable([
      { key: "label", label: "Cliente",
        fmt: v => `<span class="mono">${v}</span>` },
      { key: "proyecto", label: "Proyecto" },
      { key: "engine", label: "Tipo",
        fmt: v => engineChip(v) },
      { key: "prev_size", label: "Anterior", align: "right",
        fmt: v => `<span class="mono">${fmtBytes(v)}</span>` },
      { key: "newest_size", label: "Actual", align: "right",
        fmt: v => `<span class="mono">${fmtBytes(v)}</span>` },
      { key: "delta_pct", label: "% menor", align: "right",
        fmt: v => `<span class="chip chip-amber">${v != null ? v.toFixed(1) : "?"}%</span>` },
      { key: "newest_ts", label: "Cuándo", align: "right",
        fmt: v => `<span class="mono">${fmtTsShort(v)}</span>` },
    ], rows),
  };
}

const SPOTLIGHT_RENDERERS = {
  proyectos: spotlightProyectos,
  clientes:  () => spotlightClientes(),
  vivos:     spotlightVivos,
  archivos:  spotlightArchivos,
  tamano:    spotlightTamano,
  ultimo:    spotlightUltimo,
  encogidos: spotlightEncogidos,
};

function openSpotlight(kind) {
  if (!TREE_STATE.data) return;
  const renderer = SPOTLIGHT_RENDERERS[kind];
  if (!renderer) return;

  // Resetea sort cuando cambias de kind para que el default del renderer aplique.
  if (SPOTLIGHT.kind !== kind) {
    SPOTLIGHT.kind = kind;
    SPOTLIGHT.sortKey = null;
    SPOTLIGHT.sortAsc = false;
  }
  SPOTLIGHT.open = true;

  const out = renderer();
  $T("spotlight").classList.remove("hidden");
  $T("spotlight-title").textContent = out._customTitle || out.title;
  $T("spotlight-body").innerHTML = out.body;

  // Oculta árbol y filtros del árbol mientras el spotlight esté activo.
  $T("tree-root").classList.add("hidden");
  $T("tree-filters").classList.add("hidden");

  // Sort handlers
  $T("spotlight-body").querySelectorAll("[data-spot-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.dataset.spotSort;
      if (SPOTLIGHT.sortKey === k) SPOTLIGHT.sortAsc = !SPOTLIGHT.sortAsc;
      else { SPOTLIGHT.sortKey = k; SPOTLIGHT.sortAsc = true; }
      openSpotlight(SPOTLIGHT.kind);  // re-render
    });
  });
}

function closeSpotlight() {
  SPOTLIGHT.open = false;
  SPOTLIGHT.kind = null;
  SPOTLIGHT.sortKey = null;
  $T("spotlight").classList.add("hidden");
  $T("tree-root").classList.remove("hidden");
  $T("tree-filters").classList.remove("hidden");
}

// ---------- bootstrap ----------
document.addEventListener("DOMContentLoaded", () => {
  $T("filter-tree").addEventListener("input", (e) => {
    TREE_STATE.filter = e.target.value.trim();
    renderTree();
  });
  $T("filter-tree-proyecto").addEventListener("change", (e) => {
    TREE_STATE.filterProyecto = e.target.value;
    renderTree();
  });
  $T("filter-tree-cliente").addEventListener("change", (e) => {
    TREE_STATE.filterCliente = e.target.value;
    renderTree();
  });
  $T("filter-with-db").addEventListener("change", (e) => { TREE_STATE.withDb = e.target.checked; renderTree(); });
  $T("filter-encrypted").addEventListener("change", (e) => { TREE_STATE.encryptedOnly = e.target.checked; renderTree(); });
  $T("filter-stale").addEventListener("change", (e) => { TREE_STATE.staleOnly = e.target.checked; renderTree(); });

  const shrunkBtn = $T("filter-shrunk-only");
  if (shrunkBtn) {
    shrunkBtn.addEventListener("click", () => {
      TREE_STATE.shrunkOnly = !TREE_STATE.shrunkOnly;
      shrunkBtn.classList.toggle("btn-primary", TREE_STATE.shrunkOnly);
      shrunkBtn.classList.toggle("btn-secondary", !TREE_STATE.shrunkOnly);
      shrunkBtn.textContent = TREE_STATE.shrunkOnly ? "Mostrar todo" : "Filtrar encogidos";
      // Filtrar encogidos: si está activo, abre spotlight directo; si no, vuelve al árbol.
      if (TREE_STATE.shrunkOnly) openSpotlight("encogidos");
      else closeSpotlight();
    });
  }

  // Cards interactivas → spotlight
  document.querySelectorAll("[data-spotlight]").forEach(card => {
    card.addEventListener("click", () => {
      const kind = card.dataset.spotlight;
      if (SPOTLIGHT.open && SPOTLIGHT.kind === kind) {
        closeSpotlight();   // segundo clic en la misma card cierra
      } else {
        openSpotlight(kind);
      }
    });
  });
  $T("spotlight-close").addEventListener("click", closeSpotlight);

  // Hook al botón Refrescar → recarga el árbol y refresca spotlight si está abierto.
  const refreshBtn = $T("btn-refresh");
  refreshBtn.addEventListener("click", async () => {
    await loadTree(true);
    if (SPOTLIGHT.open) openSpotlight(SPOTLIGHT.kind);
  });

  // Carga inicial
  loadTree(false);
});

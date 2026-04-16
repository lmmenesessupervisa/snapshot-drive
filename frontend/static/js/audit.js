// Audit dashboard — agrega estados de clientes leídos del shared Drive
// vía /audit/api/status. Auto-refresh cada 30s, drill-down por fila.

const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);

const STATE = {
  data: null,
  filterText: "",
  failOnly: false,
  expanded: new Set(),
  refreshTimer: null,
};

function fmtSize(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, x = Number(n);
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function fmtDuration(s) {
  if (s == null) return "—";
  s = Math.max(0, Math.round(Number(s)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), ss = s % 60;
  if (m < 60) return `${m}m ${ss}s`;
  const h = Math.floor(m / 60), mm = m % 60;
  return `${h}h ${mm}m`;
}

function fmtAge(hours) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function fmtTs(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString("es-ES", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  } catch { return ts; }
}

function healthChip(h) {
  const cfg = {
    ok:         { bg: "bg-emerald-500/15", bd: "border-emerald-500/40", tx: "text-emerald-300", label: "OK" },
    fail:       { bg: "bg-rose-500/15",    bd: "border-rose-500/40",    tx: "text-rose-300",    label: "FALLO" },
    silent:     { bg: "bg-amber-500/15",   bd: "border-amber-500/40",   tx: "text-amber-300",   label: "SILENCIO" },
    running:    { bg: "bg-sky-500/15",     bd: "border-sky-500/40",     tx: "text-sky-300",     label: "EN CURSO" },
    unreported: { bg: "bg-violet-500/15",  bd: "border-violet-500/40",  tx: "text-violet-300",  label: "SIN REPORTAR" },
    unknown:    { bg: "bg-slate-500/15",   bd: "border-slate-500/40",   tx: "text-slate-300",   label: "—" },
  }[h] || { bg: "bg-slate-500/15", bd: "border-slate-500/40", tx: "text-slate-300", label: h };
  return `<span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold border ${cfg.bg} ${cfg.bd} ${cfg.tx}">${cfg.label}</span>`;
}

function opLabel(op) {
  return ({ create: "Crear", reconcile: "Reconciliar", prune: "Purgar", init: "Inicializar" })[op] || op || "—";
}

function clientRow(c) {
  const last = c.last || {};
  const totals = c.totals || {};
  const okCount = totals.create_count || 0;
  const failCount = totals.fail_count || 0;
  const target = last.target ? `<span class="text-[var(--muted-2)]">· ${last.target}</span>` : "";
  const tag = last.tag ? `<span class="text-[var(--muted-2)]">#${last.tag}</span>` : "";
  const expanded = STATE.expanded.has(c.host);

  return `
    <tr class="border-t border-[var(--border)] hover:bg-white/[0.03] cursor-pointer" data-host="${c.host}" data-role="row">
      <td class="py-3 px-4 font-mono text-slate-100">${c.host}</td>
      <td class="py-3 px-4">${healthChip(c.health)}</td>
      <td class="py-3 px-4 text-xs text-[var(--muted)]">
        <div>${opLabel(last.op)} ${tag}</div>
        <div class="mono text-[var(--muted-2)]">${fmtTs(last.ts)} ${target}</div>
      </td>
      <td class="py-3 px-4 text-right mono text-xs text-slate-300">${fmtTs(totals.last_successful_backup_ts)}</td>
      <td class="py-3 px-4 text-right mono text-xs ${c.silent_hours > 36 ? 'text-amber-300' : 'text-[var(--muted)]'}">${fmtAge(c.silent_hours)}</td>
      <td class="py-3 px-4 text-right mono text-xs">
        <span class="text-emerald-300">${okCount}</span>
        <span class="text-[var(--muted-2)] mx-1">/</span>
        <span class="text-rose-300">${failCount}</span>
      </td>
      <td class="py-3 px-4 pr-6 text-right">
        <svg class="w-4 h-4 inline-block transition-transform ${expanded ? 'rotate-180' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </td>
    </tr>
    ${expanded ? drilldownRow(c) : ""}
  `;
}

function drilldownRow(c) {
  if (c.health === "unreported") {
    return `
      <tr class="border-t border-[var(--border)] bg-black/30">
        <td colspan="7" class="p-0">
          <div class="px-10 py-4 text-xs text-[var(--muted)]">
            <div class="text-[11px] uppercase tracking-wider mb-2 text-violet-300">Sin reportar · ${c.host}</div>
            Existe un repo restic en el Drive para este cliente, pero todavía no
            hay un <code class="mono">_status/${c.host}.json</code>. Probablemente
            corre una versión anterior de snapctl que no escribe metadata.
            <div class="mt-2 text-[var(--muted-2)]">
              Para activarlo, en esa máquina:
              <pre class="mt-1 mono text-[11px] bg-black/40 rounded p-2 text-slate-300">cd ~/snapshot-drive &amp;&amp; git pull &amp;&amp; sudo bash install.sh
sudo snapctl create --tag post-upgrade</pre>
            </div>
          </div>
        </td>
      </tr>
    `;
  }
  const rows = (c.history || []).slice(0, 20).map(h => {
    const tx = h.status === "ok" ? "text-emerald-300" : h.status === "fail" ? "text-rose-300" : "text-sky-300";
    const err = h.error ? `<div class="text-rose-400 text-[11px] mt-0.5">${h.error}</div>` : "";
    return `
      <tr>
        <td class="py-1.5 pl-2 pr-3 mono text-xs text-[var(--muted)]">${fmtTs(h.ts)}</td>
        <td class="py-1.5 px-3 text-xs">${opLabel(h.op)}</td>
        <td class="py-1.5 px-3 text-xs ${tx} uppercase font-semibold">${h.status || "—"}</td>
        <td class="py-1.5 px-3 mono text-xs">${fmtDuration(h.duration_s)}</td>
        <td class="py-1.5 px-3 text-xs text-[var(--muted-2)]">${h.tag ? "#" + h.tag : ""} ${h.target || ""}</td>
        <td class="py-1.5 pr-2 text-xs">${err}</td>
      </tr>
    `;
  }).join("");

  const empty = `<tr><td colspan="6" class="py-3 text-center text-[var(--muted)] text-xs">Sin historial.</td></tr>`;

  return `
    <tr class="border-t border-[var(--border)] bg-black/30">
      <td colspan="7" class="p-0">
        <div class="px-10 py-4">
          <div class="text-[11px] uppercase tracking-wider text-[var(--muted)] mb-2">Historial · ${c.host}</div>
          <table class="w-full">
            ${rows || empty}
          </table>
        </div>
      </td>
    </tr>
  `;
}

function applyFilters(clients) {
  let out = clients;
  if (STATE.failOnly) {
    // "Atención" = fallidos, silenciosos o sin reportar (estados que piden acción).
    out = out.filter(c => c.health === "fail" || c.health === "silent" || c.health === "unreported");
  }
  if (STATE.filterText) {
    const needle = STATE.filterText.toLowerCase();
    out = out.filter(c => c.host.toLowerCase().includes(needle));
  }
  return out;
}

function renderTable() {
  if (!STATE.data) return;
  const tbody = $("audit-rows");
  const filtered = applyFilters(STATE.data.clients);

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-10 text-center text-[var(--muted)]">Sin resultados.</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map(clientRow).join("");

  tbody.querySelectorAll('[data-role="row"]').forEach(tr => {
    tr.addEventListener("click", () => {
      const host = tr.dataset.host;
      if (STATE.expanded.has(host)) STATE.expanded.delete(host);
      else STATE.expanded.add(host);
      renderTable();
    });
  });
}

function renderKpis() {
  const s = STATE.data?.summary || {};
  $("kpi-total").textContent      = s.total      ?? "0";
  $("kpi-ok").textContent         = s.ok         ?? "0";
  $("kpi-fail").textContent       = s.fail       ?? "0";
  $("kpi-silent").textContent     = s.silent     ?? "0";
  $("kpi-running").textContent    = s.running    ?? "0";
  $("kpi-unreported").textContent = s.unreported ?? "0";
  $("audit-last-ts").textContent  = new Date().toLocaleTimeString("es-ES", { hour12: false });
}

function showError(msg) {
  const el = $("audit-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function hideError() { $("audit-error").classList.add("hidden"); }

async function fetchStatus(force = false) {
  try {
    const url = "/audit/api/status" + (force ? "?force=1" : "");
    const resp = await fetch(url, { credentials: "same-origin" });
    if (resp.status === 401) { location.href = "/audit/login"; return; }
    const body = await resp.json();
    if (!body.ok) throw new Error(body.error || "error desconocido");
    STATE.data = body;
    hideError();
    renderKpis();
    renderTable();
  } catch (e) {
    showError(`No se pudo cargar la auditoría: ${e.message}`);
  }
}

async function hardRefresh() {
  try {
    await fetch("/audit/api/refresh", { method: "POST", credentials: "same-origin" });
  } catch {}
  await fetchStatus(true);
}

$("btn-refresh").addEventListener("click", hardRefresh);
$("filter-fail-only").addEventListener("change", (e) => {
  STATE.failOnly = e.target.checked;
  renderTable();
});
$("filter-search").addEventListener("input", (e) => {
  STATE.filterText = e.target.value.trim();
  renderTable();
});

fetchStatus();
STATE.refreshTimer = setInterval(() => fetchStatus(false), 30_000);

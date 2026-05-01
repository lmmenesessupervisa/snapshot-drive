// /audit · vista "Mis backups" — solo MODE=client.
// Lee /audit/api/local (filtrada al proyecto/entorno/pais/host de este server)
// y renderiza KPIs + tabla de backup blocks (sistema + DBs) con filtros pro.

const $ = (id) => document.getElementById(id);

const STATE = {
  data: null,
  filterText: "",
  filterType: "all",       // all|os|db
  filterEngine: "all",     // all|linux|postgres|mysql|mongo
  filterCrypto: "all",     // all|encrypted|plain
  filterShrunk: false,
};

// ---------- formatters ----------
function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, x = Number(n);
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function fmtTs(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("es-ES", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  } catch { return iso; }
}

function ageHuman(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "—";
  const h = (Date.now() - t) / 3600000;
  if (h < 1) return `hace ${Math.round(h * 60)}min`;
  if (h < 24) return `hace ${h.toFixed(1)}h`;
  return `hace ${(h / 24).toFixed(1)}d`;
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

function shrinkChip(b) {
  if (!b || !b.shrunk) return "";
  const pct = (b.shrink_delta_pct != null) ? b.shrink_delta_pct.toFixed(1) : "?";
  const prev = (b.prev_size != null) ? fmtBytes(b.prev_size) : "?";
  const curr = (b.newest_size != null) ? fmtBytes(b.newest_size) : "?";
  return `<span class="chip chip-amber" title="último: ${curr} · anterior: ${prev}">⚠ ${pct}% menor</span>`;
}

// ---------- KPIs ----------
function renderKpis() {
  const s = STATE.data?.summary || {};
  $("lk-size").textContent = fmtBytes(s.size_bytes);
  $("lk-files-sub").textContent = `${s.files ?? 0} archivo${s.files === 1 ? '' : 's'}`;
  $("lk-sys-files").textContent = s.system_files ?? 0;
  $("lk-sys-size").textContent  = fmtBytes(s.system_size);
  $("lk-db-files").textContent  = s.db_files ?? 0;
  $("lk-db-size").textContent   = fmtBytes(s.db_size);
  const totalFiles = s.files || 0;
  const enc = s.encrypted_files ?? 0;
  $("lk-encrypted").textContent = enc;
  $("lk-encrypted-sub").textContent = totalFiles
    ? `${Math.round(100 * enc / totalFiles)}% del total`
    : "0% del total";
  $("lk-last").textContent = fmtTs(s.last_backup_ts);
  $("lk-last-age").textContent = ageHuman(s.last_backup_ts);

  const sh = s.shrunk ?? 0;
  $("lk-shrunk").textContent = sh;
  $("lk-shrunk").className = "text-3xl font-semibold mt-2 " +
    (sh > 0 ? "text-amber-300" : "text-[var(--muted)]");
  $("lk-shrink-pct").textContent = s.shrink_pct_threshold ?? 20;

  const banner = $("local-shrink-banner");
  if (sh > 0) {
    $("local-shrink-msg").textContent =
      `${sh} backup${sh === 1 ? '' : 's'} más reciente${sh === 1 ? '' : 's'} pesa${sh === 1 ? '' : 'n'} al menos ${s.shrink_pct_threshold ?? 20}% menos que el anterior. Verifica que no falten directorios o tablas.`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }

  if (s.scanned_at) {
    $("audit-last-ts").textContent = new Date(s.scanned_at * 1000).toLocaleTimeString("es-ES", { hour12: false });
  }
}

// ---------- Block render ----------
function blockTitle(b) {
  if (b.category === "os" && b.subkey === "linux") return "Backup mensual del sistema";
  return `Backup DB · ${b.subkey}`;
}

function passesFilter(b) {
  if (STATE.filterType === "os" && b.category !== "os") return false;
  if (STATE.filterType === "db" && b.category !== "db") return false;
  if (STATE.filterEngine !== "all" && b.engine !== STATE.filterEngine) return false;
  if (STATE.filterShrunk && !b.shrunk) return false;
  if (STATE.filterCrypto === "encrypted" && b.encrypted_count === 0) return false;
  if (STATE.filterCrypto === "plain" && b.encrypted_count === b.count) return false;
  if (STATE.filterText) {
    const needle = STATE.filterText.toLowerCase();
    const hay =
      b.subkey.toLowerCase().includes(needle) ||
      b.category.toLowerCase().includes(needle) ||
      (b.recent || []).some(f => f.name.toLowerCase().includes(needle));
    if (!hay) return false;
  }
  return true;
}

function renderBlock(b) {
  const recent = (b.recent || []).map((f, i) => {
    const isNewest = i === 0;
    const dot = isNewest && b.shrunk
      ? '<span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5" title="encogido vs anterior"></span>'
      : '';
    return `
      <tr ${isNewest && b.shrunk ? 'class="bg-amber-500/5"' : ''}>
        <td class="mono text-xs py-1.5">${dot}${fmtTs(f.ts_iso)}</td>
        <td class="mono text-xs py-1.5">${fmtBytes(f.size)}</td>
        <td class="py-1.5">${cryptoChip(f.crypto)}</td>
        <td class="mono text-[11px] text-[var(--muted-2)] py-1.5 break-all">${f.path}</td>
      </tr>
    `;
  }).join("");

  return `
    <div class="card audit-bk-block ${b.shrunk ? 'border-amber-500/40' : ''}">
      <div class="audit-bk-header px-5 py-3">
        <div class="flex items-center gap-2 flex-wrap">
          ${engineChip(b.engine)}
          <span class="font-semibold text-sm">${blockTitle(b)}</span>
          ${shrinkChip(b)}
          ${b.encrypted_count > 0
            ? `<span class="chip chip-emerald">${b.encrypted_count}/${b.count} cifrado${b.count === 1 ? '' : 's'}</span>`
            : ''}
        </div>
        <div class="flex items-center gap-3 text-xs text-[var(--muted)] flex-wrap">
          <span>${b.count} archivo${b.count === 1 ? '' : 's'}</span>
          <span class="mono">${fmtBytes(b.size)}</span>
          <span>último: <b class="mono">${fmtTs(b.newest_ts)}</b></span>
          <span>${ageChip(b.newest_ts)}</span>
        </div>
      </div>
      <div class="px-3 pb-3">
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
    </div>
  `;
}

function renderBlocks() {
  if (!STATE.data) return;
  const root = $("local-blocks");
  const all = [];
  if (STATE.data.system) all.push(STATE.data.system);
  for (const d of STATE.data.databases || []) all.push(d);
  const filtered = all.filter(passesFilter);

  $("local-result-count").textContent =
    `${filtered.length} de ${all.length} bloque${all.length === 1 ? '' : 's'}`;

  if (!filtered.length) {
    root.innerHTML = `<div class="card p-8 text-center text-[var(--muted)]">Sin resultados con los filtros activos.</div>`;
    return;
  }
  root.innerHTML = filtered.map(renderBlock).join("");
}

// ---------- Empty / unconfigured states ----------
function renderUnconfigured() {
  const f = STATE.data?.filter || {};
  $("local-blocks").innerHTML = `
    <div class="card p-8 text-center">
      <div class="text-rose-300 font-semibold mb-2">Falta configurar la taxonomía de este cliente</div>
      <div class="text-xs text-[var(--muted)] mb-3">
        Ve a <a href="/settings" class="text-brand-500 underline">Ajustes</a> y configura
        <code class="mono">BACKUP_PROYECTO</code>, <code class="mono">BACKUP_ENTORNO</code> y
        <code class="mono">BACKUP_PAIS</code> antes de auditar tus propios backups.
      </div>
      <div class="text-[11px] text-[var(--muted-2)] mono">
        Actual: ${f.proyecto || "?"}/${f.entorno || "?"}/${f.pais || "?"}/${f.label || "?"}
      </div>
    </div>
  `;
}

function renderEmptyDrive() {
  $("local-blocks").innerHTML = `
    <div class="card p-8 text-center text-[var(--muted)]">
      <div class="font-semibold text-slate-100 mb-1">Aún no hay backups en Drive para este cliente</div>
      <div class="text-xs">
        Ejecuta <code class="mono">sudo snapctl create</code> en este host o espera al próximo timer
        (<code class="mono">snapshot@archive.timer</code>). Luego refresca esta vista.
      </div>
    </div>
  `;
}

// ---------- Fetch ----------
function showError(msg) {
  const el = $("audit-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideError() { $("audit-error").classList.add("hidden"); }

async function fetchLocal(force = false) {
  try {
    const url = "/audit/api/local" + (force ? "?force=1" : "");
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/auth/login"; return; }
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || "error desconocido");
    STATE.data = j;
    hideError();

    if (!j.configured) {
      // KPIs en cero + estado "configura primero"
      renderKpis();
      renderUnconfigured();
      return;
    }
    renderKpis();
    if ((j.summary?.files || 0) === 0) {
      renderEmptyDrive();
    } else {
      renderBlocks();
    }
  } catch (e) {
    showError("No se pudo cargar tus backups: " + e.message);
  }
}

async function hardRefresh() {
  try {
    await apiFetch("/audit/api/refresh", { method: "POST", credentials: "same-origin" });
  } catch {}
  await fetchLocal(true);
}

// ---------- Wire ----------
document.addEventListener("DOMContentLoaded", () => {
  $("btn-refresh").addEventListener("click", hardRefresh);

  $("local-filter-text").addEventListener("input", (e) => {
    STATE.filterText = e.target.value.trim(); renderBlocks();
  });
  $("local-filter-type").addEventListener("change", (e) => {
    STATE.filterType = e.target.value; renderBlocks();
  });
  $("local-filter-engine").addEventListener("change", (e) => {
    STATE.filterEngine = e.target.value; renderBlocks();
  });
  $("local-filter-crypto").addEventListener("change", (e) => {
    STATE.filterCrypto = e.target.value; renderBlocks();
  });
  $("local-filter-shrunk").addEventListener("change", (e) => {
    STATE.filterShrunk = e.target.checked; renderBlocks();
  });

  // KPIs clickeables → preset de filtros
  document.querySelectorAll("[data-local-filter]").forEach(el => {
    el.addEventListener("click", () => {
      const v = el.dataset.localFilter;
      if (v === "os" || v === "db") {
        $("local-filter-type").value = v;
        STATE.filterType = v;
      } else if (v === "shrunk") {
        $("local-filter-shrunk").checked = true;
        STATE.filterShrunk = true;
      }
      renderBlocks();
    });
  });

  fetchLocal();
  // Auto-refresh suave cada 2 min mientras la pestaña esté visible.
  if (typeof autoRefresh === "function") {
    autoRefresh(() => fetchLocal(false), 120_000);
  }
});

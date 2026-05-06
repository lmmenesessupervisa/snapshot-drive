// Dashboard — archives mensuales.
//
// Llama a /api/archive/summary para poblar KPIs y el detalle del último.
// Auto-refresh cada 60s. Botón "Generar archivo ahora" dispara POST
// /api/archive/create y muestra overlay de progreso (operación puede
// tardar minutos en servers grandes).

const $ = (id) => document.getElementById(id);

function fmtBytes(n) {
  if (!n || isNaN(n)) return "0 B";
  const u = ["B","KB","MB","GB","TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + " " + u[i];
}

function ago(isoTs) {
  if (!isoTs) return "—";
  try {
    const d = new Date(isoTs);
    const secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 60)    return `hace ${secs}s`;
    if (secs < 3600)  return `hace ${Math.floor(secs/60)}m`;
    if (secs < 86400) return `hace ${Math.floor(secs/3600)}h`;
    return `hace ${Math.floor(secs/86400)}d`;
  } catch { return isoTs; }
}

function fmtFullTs(isoTs) {
  if (!isoTs) return "—";
  try {
    const d = new Date(isoTs);
    return d.toLocaleString("es-ES", {
      year:"numeric", month:"2-digit", day:"2-digit",
      hour:"2-digit", minute:"2-digit", hour12:false,
    });
  } catch { return isoTs; }
}

// Convierte el NextElapseUSecRealtime de systemctl a texto legible.
//   "Fri 2026-05-01 02:04:44 UTC" → pasa tal cual (ya legible).
function fmtNextRun(raw) {
  if (!raw) return "—";
  return raw.replace(/^(\w+) /, "");
}

function renderLastDetail(last) {
  if (!last) {
    $("last-detail").innerHTML = `
      <div class="text-[var(--muted)]">Aún no se ha creado ningún archivo.</div>
      <div class="mt-3 text-xs text-[var(--muted-2)]">
        Pulsa "Generar archivo ahora" o espera al primer timer del día 1.
      </div>`;
    return;
  }
  const encChip = last.encrypted
    ? `<span class="chip chip-emerald">AES-256</span>`
    : `<span class="chip chip-slate">sin encriptar</span>`;
  $("last-detail").innerHTML = `
    <div class="mono text-[13px] text-[var(--foreground)]">${last.name}</div>
    <div class="mono text-[11px] text-[var(--muted-2)] mt-1 break-all">${last.path}</div>
    <div class="flex flex-wrap items-center gap-4 mt-4 text-xs">
      <div><span class="text-[var(--muted)]">Tamaño:</span> <b class="mono">${fmtBytes(last.size_bytes)}</b></div>
      <div><span class="text-[var(--muted)]">Subido:</span> <b class="mono">${fmtFullTs(last.modified_ts)}</b></div>
      <div>${encChip}</div>
    </div>
  `;
}

function render(s) {
  $("kpi-count").textContent = s.archives_count ?? "0";
  $("kpi-size").textContent  = fmtBytes(s.total_size_bytes || 0);

  if (s.last) {
    $("kpi-last-ago").textContent = ago(s.last.modified_ts);
    $("kpi-last-sub").textContent = fmtFullTs(s.last.modified_ts);
    $("last-when").textContent = fmtFullTs(s.last.modified_ts);
  } else {
    $("kpi-last-ago").textContent = "—";
    $("kpi-last-sub").textContent = "sin archivos aún";
    $("last-when").textContent = "—";
  }
  renderLastDetail(s.last);

  $("kpi-next").textContent = fmtNextRun(s.next_scheduled) || "—";

  if (s.drive_path_root) {
    $("drive-root-preview").textContent = `${s.drive_path_root}/…`;
  } else {
    $("drive-root-preview").textContent = "(taxonomía sin configurar)";
  }

  $("dash-setup-warning").classList.toggle("hidden", !!s.taxonomy_ok);
}

async function load() {
  try {
    // Muestra cache al instante si hay, refetch en background con TTL 60s.
    await cachedFetch("archive:summary", "/archive/summary", 60_000, (s) => render(s));
  } catch (e) {
    toast(`Error cargando dashboard: ${e.message}`, "error");
  }
}

$("btn-create").onclick = async () => {
  if (!confirm("Se va a generar un archivo .tar.zst AHORA y subir a Drive. Puede tardar varios minutos. ¿Continuar?")) return;
  const end = busyStart("Generando archivo…");
  try {
    const res = await API.post("/archive/create", {});
    toast(`Archivo creado en ${res.duration_s}s`, "success");
    Cache.invalidate("archive:");           // el backend ya lo invalidó también
    const fresh = await API.get("/archive/summary?force=1");
    Cache.set("archive:summary", fresh);
    render(fresh);
  } catch (e) {
    toast(`Error: ${e.message}`, "error");
  } finally { end(); }
};

// ============================================================
// DB backup: KPI + smart button + mini-grid
// ============================================================
const ENGINE_LABEL = { postgres: "PostgreSQL", mysql: "MySQL/MariaDB", mongo: "MongoDB" };
const ENGINE_CHIP  = { postgres: "chip-sky", mysql: "chip-amber", mongo: "chip-emerald" };
let DB_SUMMARY = null;

function ageChip(isoTs) {
  if (!isoTs) return '<span class="chip chip-slate">sin datos</span>';
  const days = (Date.now() - new Date(isoTs).getTime()) / 86_400_000;
  if (days < 1) return '<span class="chip chip-emerald">&lt;24h</span>';
  if (days < 2) return '<span class="chip chip-amber">' + days.toFixed(1) + 'd</span>';
  return '<span class="chip chip-rose">' + days.toFixed(0) + 'd</span>';
}

function renderDb(summary) {
  DB_SUMMARY = summary;
  const engines = summary.configured_engines || [];
  const last = summary.last_per_engine || {};

  // KPI
  if (summary.last_overall_ts) {
    $("kpi-db-ago").textContent = ago(summary.last_overall_ts);
    $("kpi-db-sub").textContent = fmtFullTs(summary.last_overall_ts);
  } else if (engines.length) {
    $("kpi-db-ago").textContent = "—";
    $("kpi-db-sub").textContent = "configurado, sin dumps aún";
  } else {
    $("kpi-db-ago").textContent = "—";
    $("kpi-db-sub").textContent = "sin engines configurados";
  }

  // Botón inteligente
  const btn = $("btn-db-create");
  if (engines.length === 0) {
    btn.disabled = true;
    btn.title = "Configura un engine en Ajustes → Backups de bases de datos";
  } else {
    btn.disabled = false;
    btn.title = engines.length === 1
      ? `Genera backup de ${ENGINE_LABEL[engines[0]] || engines[0]}`
      : `Selecciona qué engines (${engines.length} configurados)`;
  }

  // Mini-grid
  const card = $("db-engines-card");
  const grid = $("db-engines-grid");
  if (!engines.length) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  grid.innerHTML = engines.map((e) => {
    const info = last[e];
    const sub = info
      ? `${fmtFullTs(info.ts_iso)} · ${fmtBytes(info.size_bytes)}`
      : "sin dumps todavía";
    return `
      <div class="rounded-lg border border-[var(--border)] p-3">
        <div class="flex items-center gap-2">
          <span class="chip ${ENGINE_CHIP[e] || 'chip-slate'}">${e}</span>
          ${ageChip(info && info.ts_iso)}
        </div>
        <div class="text-xs text-[var(--muted)] mt-2 mono break-all">${sub}</div>
      </div>
    `;
  }).join("");
}

async function loadDb() {
  try {
    const s = await API.get("/db-archive/summary");
    renderDb(s);
  } catch (e) {
    // No tirar el dashboard entero si falla; solo dejar el estado por defecto.
    console.warn("db-archive/summary error:", e.message);
  }
}

async function runDbBackup(engines) {
  const list = engines && engines.length ? engines : (DB_SUMMARY?.configured_engines || []);
  if (!list.length) { toast("No hay engines configurados", "warn"); return; }
  if (!confirm(`Se va a generar dump${list.length>1?'s':''} para: ${list.join(", ")}. ¿Continuar?`)) return;
  const end = busyStart("Generando backup BD…");
  try {
    const res = await API.post("/db-archive/create", { engines: list });
    toast(`DB backup: ${res.ok_count} ok, ${res.fail_count} fail (${res.duration_s}s)`,
          res.fail_count === 0 ? "success" : "warn");
    await loadDb();
  } catch (e) {
    toast(`Error: ${e.message}`, "error");
  } finally { end(); }
}

$("btn-db-create").onclick = () => {
  const engines = DB_SUMMARY?.configured_engines || [];
  if (engines.length === 0) return;
  if (engines.length === 1) { runDbBackup([engines[0]]); return; }
  // 2+: abrir modal selector
  const opts = $("db-pick-options");
  opts.innerHTML = engines.map((e) => `
    <label class="flex items-center gap-2 cursor-pointer">
      <input type="checkbox" name="engine" value="${e}" checked class="accent-[var(--primary)]">
      <span class="chip ${ENGINE_CHIP[e] || 'chip-slate'}">${e}</span>
      <span class="text-xs text-[var(--muted)]">${ENGINE_LABEL[e] || e}</span>
    </label>
  `).join("");
  $("dlg-db-pick").showModal();
};
$("db-pick-cancel").onclick = () => $("dlg-db-pick").close();
$("form-db-pick").addEventListener("submit", (e) => {
  e.preventDefault();
  const picked = Array.from(e.target.querySelectorAll('input[name="engine"]:checked'))
    .map(i => i.value);
  $("dlg-db-pick").close();
  if (picked.length) runDbBackup(picked);
});

load();
loadDb();
// Auto-refresh solo cuando la pestaña está visible.
autoRefresh(load, 120_000);
autoRefresh(loadDb, 120_000);

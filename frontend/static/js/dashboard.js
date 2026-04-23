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

async function load() {
  try {
    const s = await API.get("/archive/summary");
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
    await load();
  } catch (e) {
    toast(`Error: ${e.message}`, "error");
  } finally { end(); }
};

load();
setInterval(load, 60_000);

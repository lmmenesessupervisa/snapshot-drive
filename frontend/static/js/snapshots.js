// Archivos mensuales: listado + crear + restaurar + borrar.

const $ = (id) => document.getElementById(id);
const STATE = { archives: [], selected: null };

function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B","KB","MB","GB","TB"];
  let i = 0, x = Number(n);
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function fmtTs(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    return d.toLocaleString("es-ES", { year:"numeric", month:"2-digit", day:"2-digit",
                                       hour:"2-digit", minute:"2-digit", hour12:false });
  } catch { return ts; }
}

function rowHtml(a) {
  const encChip = a.encrypted
    ? `<span class="chip chip-emerald">AES-256</span>`
    : `<span class="chip chip-slate">sin encriptar</span>`;
  return `
    <tr data-path="${encodeURIComponent(a.path)}">
      <td class="text-left">
        <div class="mono text-[13px] text-[var(--foreground)]">${a.name}</div>
        <div class="mono text-[11px] text-[var(--muted-2)] mt-0.5">${a.path}</div>
      </td>
      <td class="text-left mono text-xs">${fmtTs(a.modified_ts)}</td>
      <td class="text-right mono">${fmtBytes(a.size_bytes)}</td>
      <td class="text-left">${encChip}</td>
      <td class="text-right pr-5">
        <div class="inline-flex gap-2">
          <button data-action="restore" class="btn-secondary text-[11px] !py-1 !px-2">
            Restaurar
          </button>
          <button data-action="delete" class="btn-danger text-[11px] !py-1 !px-2">
            Eliminar
          </button>
        </div>
      </td>
    </tr>
  `;
}

function renderList(list) {
  const tbody = $("arch-body");
  STATE.archives = list || [];
  if (!STATE.archives.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="px-5 py-10 text-center text-[var(--muted)]">
      Aún no hay archivos en Drive. Pulsa <b>Generar archivo ahora</b> para crear el primero.
    </td></tr>`;
    return;
  }
  tbody.innerHTML = STATE.archives.map(rowHtml).join("");
}

async function load() {
  const tbody = $("arch-body");
  // Muestra último estado conocido al instante si hay cache.
  const cachedList = Cache.get("archive:list", null);
  if (cachedList) renderList(cachedList);
  else tbody.innerHTML = `<tr><td colspan="5" class="px-5 py-10 text-center text-[var(--muted)]">Cargando…</td></tr>`;

  try {
    const cfg = await API.get("/archive/config");
    if (!cfg.proyecto || !cfg.entorno || !cfg.pais) {
      $("archive-setup-warning").classList.remove("hidden");
      tbody.innerHTML = `<tr><td colspan="5" class="px-5 py-10 text-center text-[var(--muted)]">Configura la taxonomía para empezar.</td></tr>`;
      return;
    }
    $("archive-setup-warning").classList.add("hidden");

    await cachedFetch("archive:list", "/archive/list", 60_000, (list) => renderList(list));
  } catch (e) {
    if (!cachedList) {
      tbody.innerHTML = `<tr><td colspan="5" class="px-5 py-10 text-center" style="color:#b91c1c">Error: ${e.message}</td></tr>`;
    }
  }
}

// --- Create ---
$("btn-create-archive").onclick = async () => {
  if (!confirm("Se va a generar un archivo .tar.zst AHORA y subir a Drive. En servidores grandes puede tardar varios minutos. ¿Continuar?")) return;
  const end = busyStart("Generando archivo…");
  try {
    const res = await API.post("/archive/create", {});
    toast(`Archivo creado en ${res.duration_s}s`, "success");
    Cache.invalidate("archive:");
    await load();
  } catch (e) {
    toast(`Error al crear: ${e.message}`, "error");
  } finally { end(); }
};

// --- Restore ---
function openRestore(a) {
  STATE.selected = a;
  $("restore-path").value = a.path;
  $("restore-target").value = "";
  $("restore-warning-enc").classList.toggle("hidden", !a.encrypted);
  $("modal-restore").classList.replace("hidden", "grid");
}
function closeRestore() { $("modal-restore").classList.replace("grid", "hidden"); }
$("restore-cancel").onclick = closeRestore;
$("restore-go").onclick = async () => {
  const target = ($("restore-target").value || "").trim();
  if (!target) return toast("Indica un directorio destino", "error");
  closeRestore();
  const end = busyStart("Restaurando archivo…");
  try {
    const res = await API.post("/archive/restore", {
      path: STATE.selected.path,
      target,
    });
    toast(`Restaurado en ${res.duration_s}s a ${res.target}`, "success");
  } catch (e) {
    toast(`Error: ${e.message}`, "error");
  } finally { end(); }
};

// --- Delete ---
function openDelete(a) {
  STATE.selected = a;
  $("delete-path").textContent = a.path;
  $("modal-delete").classList.replace("hidden", "grid");
}
function closeDelete() { $("modal-delete").classList.replace("grid", "hidden"); }
$("delete-cancel").onclick = closeDelete;
$("delete-go").onclick = async () => {
  try {
    await API.post("/archive/delete", { path: STATE.selected.path });
    toast("Archivo eliminado", "success");
    closeDelete();
    Cache.invalidate("archive:");
    await load();
  } catch (e) { toast(`Error: ${e.message}`, "error"); }
};

// Delegación de eventos sobre la tabla.
$("arch-body").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const tr = btn.closest("tr[data-path]");
  const path = decodeURIComponent(tr.dataset.path);
  const a = STATE.archives.find(x => x.path === path);
  if (!a) return;
  if (btn.dataset.action === "restore") openRestore(a);
  if (btn.dataset.action === "delete")  openDelete(a);
});

$("btn-refresh").onclick = load;

load();

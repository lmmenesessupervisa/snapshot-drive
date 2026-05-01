// /central/drive — vincular Drive al central + configurar AUDIT_REMOTE_PATH.
// El operador genera el token con `rclone authorize "drive"` en su
// workstation y lo pega aquí. El backend (POST /api/drive/link) lo
// escribe directo a rclone.conf; rclone se encarga del refresh del token.

const $ = (id) => document.getElementById(id);

let lastTarget = "personal";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderStatus(s) {
  const linked = !!s.linked;
  $("cd-status").innerHTML = `
    <div class="flex items-center gap-3">
      <span class="inline-flex h-2.5 w-2.5 rounded-full ${linked ? "bg-emerald-500" : "bg-rose-500"}"></span>
      <div>
        <div class="font-semibold">${linked ? "Vinculado a Google Drive" : "Sin vincular"}</div>
        <div class="text-xs text-[var(--muted)]">
          ${linked
            ? (s.team_drive
                ? 'Unidad compartida <code class="mono">' + escapeHtml(s.team_drive) + '</code>'
                : "Mi unidad personal")
            : "Pega el token de rclone para vincular."}
        </div>
      </div>
    </div>
  `;
  if (s.team_drive) {
    document.querySelector('input[name="cd-target"][value="shared"]').checked = true;
    $("cd-shared-wrap").classList.remove("hidden");
    lastTarget = "shared";
  } else if (linked) {
    document.querySelector('input[name="cd-target"][value="personal"]').checked = true;
    $("cd-shared-wrap").classList.add("hidden");
    lastTarget = "personal";
  }
}

async function loadStatus() {
  try { renderStatus(await API.get("/drive/status")); }
  catch (e) { $("cd-status").textContent = "Error: " + e.message; }
}

async function loadCentralConfig() {
  try {
    const c = await API.get("/central/config");
    // Conservar el valor real (incluido vacío = raíz del Drive).
    $("cd-remote-path").value = c.audit_remote_path || "";
    $("cd-viewer-toggle").checked = !!c.audit_viewer_enabled;
  } catch (e) {
    toast("No pude leer la config central: " + e.message, "error");
  }
}

// ---------- Manual link (paste rclone token) ----------
function setLinkMsg(text, kind) {
  const el = $("cd-link-msg");
  el.textContent = text;
  el.classList.remove("hidden", "text-rose-500", "text-emerald-500", "text-[var(--muted)]");
  if (kind === "error") el.classList.add("text-rose-500");
  else if (kind === "ok") el.classList.add("text-emerald-500");
  else el.classList.add("text-[var(--muted)]");
}

function clearLinkMsg() { $("cd-link-msg").classList.add("hidden"); }

function validateToken(raw) {
  let parsed;
  try { parsed = JSON.parse(raw); }
  catch (e) { return "El texto no es JSON válido. Pega el bloque completo que rclone te imprimió, incluyendo las llaves { }."; }
  if (typeof parsed !== "object" || parsed === null) {
    return "El JSON debe ser un objeto.";
  }
  if (!parsed.access_token) return "Falta access_token en el JSON.";
  if (!parsed.refresh_token) {
    return "Falta refresh_token. Sin él, el token expira en ~1h y el central perdería acceso. Re-corre `rclone authorize \"drive\"`.";
  }
  return null;
}

async function linkManual() {
  clearLinkMsg();
  const raw = ($("cd-token").value || "").trim();
  if (!raw) {
    setLinkMsg("Pega el JSON del token primero.", "error");
    return;
  }
  const err = validateToken(raw);
  if (err) { setLinkMsg(err, "error"); return; }
  setLinkMsg("Vinculando…");
  try {
    await API.post("/drive/link", { token: raw });
    setLinkMsg("Drive vinculado.", "ok");
    $("cd-token").value = "";
    toast("Drive vinculado.", "success");
    await loadStatus();
  } catch (e) {
    setLinkMsg("Error: " + e.message, "error");
    toast("No se pudo vincular: " + e.message, "error");
  }
}

async function unlink() {
  if (!confirm("¿Desvincular Drive? La auditoría dejará de funcionar.")) return;
  try {
    await API.post("/drive/unlink");
    clearLinkMsg();
    toast("Drive desvinculado.", "success");
    await loadStatus();
  } catch (e) {
    toast("No se pudo desvincular: " + e.message, "error");
  }
}

// ---------- Target (personal / shared) ----------
async function refreshSharedDrives() {
  const sel = $("cd-shared-select");
  sel.innerHTML = '<option value="">cargando…</option>';
  try {
    const list = await API.get("/drive/shared");
    if (!list || !list.length) {
      sel.innerHTML = '<option value="">(no hay Shared Drives accesibles)</option>';
      return;
    }
    sel.innerHTML = list.map(d =>
      `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name)} · ${escapeHtml(d.id)}</option>`,
    ).join("");
  } catch (e) {
    sel.innerHTML = `<option value="">error: ${escapeHtml(e.message)}</option>`;
  }
}

async function saveTarget() {
  const value = (document.querySelector('input[name="cd-target"]:checked') || {}).value || "personal";
  const body = { type: value };
  if (value === "shared") body.id = $("cd-shared-select").value || "";
  try {
    await API.post("/drive/target", body);
    toast("Destino actualizado.", "success");
    await loadStatus();
  } catch (e) {
    toast("Error: " + e.message, "error");
  }
}

// ---------- AUDIT_REMOTE_PATH ----------
async function savePath() {
  const path = ($("cd-remote-path").value || "").trim();
  const enabled = $("cd-viewer-toggle").checked;
  try {
    await API.post("/central/config", {
      audit_remote_path: path,
      audit_viewer_enabled: enabled,
    });
    $("cd-path-saved-hint").textContent = "guardado · cache invalidado";
    toast("Configuración guardada.", "success");
  } catch (e) {
    toast("Error: " + e.message, "error");
  }
}

// ---------- Wire up ----------
document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  loadCentralConfig();

  $("cd-refresh").addEventListener("click", loadStatus);
  $("cd-link").addEventListener("click", linkManual);
  $("cd-unlink").addEventListener("click", unlink);

  document.querySelectorAll('input[name="cd-target"]').forEach(r => {
    r.addEventListener("change", () => {
      const v = r.value;
      $("cd-shared-wrap").classList.toggle("hidden", v !== "shared");
      if (v === "shared") refreshSharedDrives();
    });
  });
  $("cd-refresh-shared").addEventListener("click", refreshSharedDrives);
  $("cd-save-target").addEventListener("click", saveTarget);

  $("cd-save-path").addEventListener("click", savePath);
});

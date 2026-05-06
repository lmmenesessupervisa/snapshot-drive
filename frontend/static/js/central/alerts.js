// /dashboard-central/alerts — wire-up de la tabla de alertas.
// El template ya hace el grueso del trabajo (etiquetas humanas, detalles
// traducidos por tipo). Este JS solo:
//   1. Reemplaza el timestamp UTC por una versión amigable
//      "hace 13 min · 6 may 2026 16:13" (manteniendo el ISO original
//      en data-iso para tooltip y debugging).
//   2. Wire del botón "Reconocer" (acknowledge).

function _humanizeRelative(iso) {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 60)    return `hace ${secs}s`;
    if (secs < 3600)  return `hace ${Math.floor(secs / 60)} min`;
    if (secs < 86400) return `hace ${Math.floor(secs / 3600)} h`;
    if (secs < 604800) return `hace ${Math.floor(secs / 86400)} d`;
    return d.toLocaleDateString("es-ES", { year: "numeric", month: "short", day: "numeric" });
  } catch { return iso; }
}

function _humanizeAbsolute(iso) {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("es-ES", {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  } catch { return iso; }
}

document.querySelectorAll(".audit-time-rel").forEach((el) => {
  const iso = el.dataset.iso;
  if (!iso) return;
  const rel = _humanizeRelative(iso);
  const abs = _humanizeAbsolute(iso);
  el.innerHTML = `<span class="font-medium">${rel}</span><br><span class="mono text-[10px] text-[var(--muted-2)]">${abs}</span>`;
  el.title = `UTC: ${iso}\nLocal: ${abs}`;
});

document.querySelectorAll(".ack-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const tr = btn.closest("tr");
    const aid = tr.dataset.alertId;
    const cliente = tr.querySelector("td:nth-child(3) .font-medium")?.textContent || "?";
    const tipo = tr.querySelector("td:nth-child(1) .font-medium")?.textContent || "?";
    if (!confirm(
      `¿Reconocer esta alerta?\n\n  Tipo: ${tipo}\n  Cliente: ${cliente}\n\n` +
      `Esto la cierra como "atendida". Solo hacelo si ya verificaste la causa.\n` +
      `(El sistema NO va a re-disparar si la condición persiste — verifica antes.)`
    )) return;
    const r = await apiFetch(`/api/admin/alerts/${aid}/acknowledge`, { method: "POST" });
    if (r.ok) location.reload();
    else alert("Error al reconocer la alerta. Mira la consola.");
  });
});

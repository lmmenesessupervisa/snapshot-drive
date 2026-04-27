// Auto-refresh visibility-aware. Usa el mismo patrón del dashboard local.
async function refreshDashboard() {
  if (document.hidden) return;
  try {
    const r = await fetch("/api/admin/clients", {credentials: "same-origin"});
    if (!r.ok) return;
    const json = await r.json();
    if (!json.ok) return;
    location.reload();
  } catch (e) { /* network blip, retry next tick */ }
}
setInterval(refreshDashboard, 30000);
